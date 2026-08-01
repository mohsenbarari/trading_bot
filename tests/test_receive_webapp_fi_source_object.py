"""Focused local tests for the controller FI source-object receiver."""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


receiver = _load("test_receive_webapp_fi_source_object", "receive_webapp_fi_source_object.py")
static_adopter = _load("test_receive_static_adopter", "adopt_webapp_fi_static_assets.py")
image_adopter = _load("test_receive_image_adopter", "adopt_webapp_fi_controller_image.py")


def recipient(character: str) -> str:
    return "age1" + character * 40


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


class FakeBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload) - self.offset
        value = self.payload[self.offset : self.offset + size]
        self.offset += len(value)
        return value

    def close(self) -> None:
        self.closed = True


class FakeS3:
    def __init__(self, *, key: str, version_id: str, ciphertext: bytes, metadata: dict[str, str]) -> None:
        self.key = key
        self.version_id = version_id
        self.ciphertext = ciphertext
        self.metadata = metadata
        self.returned_version_id = version_id
        self.returned_ciphertext = ciphertext
        self.returned_metadata: object = metadata
        self.returned_sse: object | None = None
        self.returned_content_length: object = len(ciphertext)
        self.get_calls: list[dict[str, object]] = []
        self.bucket_calls = 0

    def get_bucket_versioning(self, **_kwargs: object) -> dict[str, object]:
        self.bucket_calls += 1
        return {"Status": "Enabled"}

    def get_bucket_acl(self, **_kwargs: object) -> dict[str, object]:
        return {
            "Owner": {"ID": "owner"},
            "Grants": [{"Grantee": {"Type": "CanonicalUser", "ID": "owner"}, "Permission": "FULL_CONTROL"}],
        }

    def list_object_versions(self, **_kwargs: object) -> dict[str, object]:
        return {
            "Versions": [{"Key": self.key, "VersionId": self.version_id, "IsLatest": True}],
            "DeleteMarkers": [],
            "IsTruncated": False,
        }

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.get_calls.append(dict(kwargs))
        response: dict[str, object] = {
            "VersionId": self.returned_version_id,
            "Metadata": self.returned_metadata,
            "ContentLength": self.returned_content_length,
            "Body": FakeBody(self.returned_ciphertext),
        }
        if self.returned_sse is not None:
            response["ServerSideEncryption"] = self.returned_sse
        return response


class SourceObjectReceiverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="source-object-receiver-test-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.data_root = self.root / "staging-data" / "controller" / "webapp-fi-source-receive"
        self.data_root.mkdir(mode=0o700, parents=True)
        for directory in (
            self.root / "staging-data",
            self.root / "staging-data" / "controller",
            self.data_root,
        ):
            directory.chmod(0o700)
        self._data_root_patch = mock.patch.object(receiver, "CONTROLLER_SOURCE_RECEIVE_ROOT", self.data_root)
        self._data_root_patch.start()
        self.campaign_id = "source-receiver-fixture-20260730"
        self.campaign_directory = self.root / self.campaign_id
        self.campaign_directory.mkdir(mode=0o700)
        self.campaign_directory.chmod(0o700)
        self.application_release_sha = "a" * 40
        self.application_release_tree = "b" * 40
        self.control_commit = "c" * 40
        self.control_tree = "d" * 40
        self.binding = self._write_binding()
        self.workspace_root = self.root / "workspaces"
        self.workspace_root.mkdir(mode=0o700)
        self.workspace_root.chmod(0o700)
        self._workspace_root_patch = mock.patch.object(
            receiver.transport.contract,
            "SOURCE_TRANSPORT_WORKSPACE_ROOT",
            self.workspace_root,
        )
        self._workspace_root_patch.start()
        self.workspace = receiver.transport.contract.source_transport_workspace_for_campaign(self.campaign_id)
        self.workspace.mkdir(mode=0o700)
        self.workspace.chmod(0o700)
        self.credentials = _private(self.root / "credentials.json", _canonical({"fixture": True}))
        self.policy = receiver.transport.SourceTransportPolicy(
            endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
            region="ir-thr-at1",
            bucket="private-artifacts",
            prefix="campaigns/three-site",
            age_binary="/usr/bin/age",
            workspace=self.workspace,
            controller_age_recipient=recipient("a"),
            webapp_fi_age_recipient=recipient("c"),
            webapp_ir_age_recipient=recipient("d"),
            maximum_plaintext_bytes=1024 * 1024,
        )
        self.controller_config = receiver.transport.ControllerS3Config(
            policy=self.policy,
            credentials_file=self.credentials,
            campaign_id=self.campaign_id,
        )
        self.test_age_keygen = self.root / "age-keygen-test-only"
        self.test_age_keygen.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        self.test_age_keygen.chmod(0o700)
        self._identity_root_patch = mock.patch.object(receiver.identity_bootstrap, "CAMPAIGNS_ROOT", self.root)
        self._identity_binary_patch = mock.patch.object(
            receiver.identity_bootstrap,
            "AGE_KEYGEN_BINARY",
            self.test_age_keygen,
        )
        self._identity_recipient_patch = mock.patch.object(
            receiver.identity_bootstrap,
            "derive_recipient",
            return_value=self.policy.controller_age_recipient,
        )
        self._identity_root_patch.start()
        self._identity_binary_patch.start()
        self._identity_recipient_patch.start()
        layout = receiver.identity_bootstrap.identity_layout_for_campaign_binding(self.binding)
        self.identity = _private(layout.identity_path, b"AGE-SECRET-KEY-1TEST\n")
        layout.controller_directory.chmod(0o700)
        receipt = receiver.identity_bootstrap._receipt_value(
            layout=layout,
            recipient=self.policy.controller_age_recipient,
        )
        self.identity_receipt = _private(layout.receipt_path, _canonical(receipt))
        self.inputs = self.root / "inputs"
        self.inputs.mkdir(mode=0o700)
        self.inputs.chmod(0o700)

    def tearDown(self) -> None:
        self._data_root_patch.stop()
        self._identity_recipient_patch.stop()
        self._identity_binary_patch.stop()
        self._identity_root_patch.stop()
        self._workspace_root_patch.stop()
        self.temporary.cleanup()

    def _write_binding(self) -> Path:
        source_phase = self.campaign_directory / receiver.binding.SOURCE_PHASE_DIRECTORY
        source_phase.mkdir(mode=0o700)
        source_phase.chmod(0o700)
        value = receiver.binding.build_campaign_binding(
            campaign_id=self.campaign_id,
            application_release_sha=self.application_release_sha,
            application_release_tree=self.application_release_tree,
            expected_alembic_revision="a" * 12,
            control_commit=self.control_commit,
            control_tree=self.control_tree,
        )
        return _private(source_phase / receiver.binding.CAMPAIGN_BINDING_FILENAME, _canonical(value))

    def _exchange_policy(self):
        return receiver._policy_for_exchange(self.policy)

    def _write_report(
        self,
        *,
        object_kind: str = receiver.contract.STATIC_OBJECT_KIND,
        object_id: str = "source-object-fixture",
        release_sha: str | None = None,
        recipients: tuple[str, ...] | None = None,
        plaintext: bytes = b"deterministic source payload\n",
        ciphertext: bytes = b"fake age ciphertext envelope\n",
    ) -> tuple[Path, dict[str, object], bytes, bytes]:
        if object_kind == receiver.contract.STATIC_OBJECT_KIND:
            destination_site = receiver.contract.STATIC_DESTINATION_SITE
            mode = receiver.contract.STATIC_MODE
            canonical_recipients = (
                self.policy.controller_age_recipient,
                self.policy.webapp_ir_age_recipient,
            )
            chosen_recipients = recipients or canonical_recipients
        else:
            destination_site = "controller"
            mode = receiver.contract.SINGLE_MODE
            canonical_recipients = (self.policy.controller_age_recipient,)
            chosen_recipients = recipients or canonical_recipients
        request = receiver.exchange.contract.SourceObjectRequest(
            campaign_id=self.campaign_id,
            release_sha=release_sha or self.application_release_sha,
            control_commit=self.control_commit,
            control_tree=self.control_tree,
            source_site="webapp_fi",
            destination_site=destination_site,
            object_kind=object_kind,
            object_id=object_id,
            mode=mode,
            recipients=chosen_recipients,
        )
        canonical_request = receiver.exchange.contract.SourceObjectRequest(
            campaign_id=request.campaign_id,
            release_sha=request.release_sha,
            control_commit=request.control_commit,
            control_tree=request.control_tree,
            source_site=request.source_site,
            destination_site=request.destination_site,
            object_kind=request.object_kind,
            object_id=request.object_id,
            mode=request.mode,
            recipients=canonical_recipients,
        )
        exchange_policy = self._exchange_policy()
        descriptor = {
            "object_key": receiver.exchange.contract.source_object_key(exchange_policy, canonical_request),
            "version_id": "version-" + object_id,
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "ciphertext_bytes": len(ciphertext),
            "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
            "plaintext_bytes": len(plaintext),
        }
        unsigned = receiver.exchange._upload_report_unsigned(request=request, descriptor=descriptor)
        report = {**unsigned, "report_sha256": receiver.exchange.sha256_bytes(receiver.exchange.canonical_json_bytes(unsigned))}
        path = _private(self.inputs / ("upload-" + object_id + ".json"), _canonical(report))
        return path, report, plaintext, ciphertext

    def _client_for_report(self, report: dict[str, object], ciphertext: bytes) -> FakeS3:
        request = report["request"]
        assert isinstance(request, dict)
        descriptor = report["object"]
        assert isinstance(descriptor, dict)
        return FakeS3(
            key=str(descriptor["object_key"]),
            version_id=str(descriptor["version_id"]),
            ciphertext=ciphertext,
            metadata=receiver.transport._ciphertext_metadata(
                str(descriptor["ciphertext_sha256"]),
                str(request["recipient_mode"]),
            ),
        )

    @staticmethod
    def _copy_decrypt(_plan: object, _ciphertext: Path, plaintext: Path, payload: bytes) -> None:
        plaintext.write_bytes(payload)
        plaintext.chmod(0o600)

    def _receive(self, *, report_path: Path, client: FakeS3, payload: bytes) -> dict[str, object]:
        return receiver.receive_fi_source_object(
            client,
            controller_config=self.controller_config,
            campaign_binding_path=self.binding,
            upload_report_path=report_path,
            decryptor=lambda plan, ciphertext, plaintext: self._copy_decrypt(
                plan, ciphertext, plaintext, payload
            ),
        )

    def test_static_receive_emits_exact_existing_static_readback_schema(self) -> None:
        report_path, report, plaintext, ciphertext = self._write_report(object_id="static-receive-fixture")
        client = self._client_for_report(report, ciphertext)

        result = self._receive(report_path=report_path, client=client, payload=plaintext)

        candidate = Path(str(result["candidate_directory"]))
        record_path = Path(str(result["readback_record"]))
        self.assertTrue((candidate / receiver.CIPHERTEXT_NAME).is_file())
        self.assertEqual(plaintext, (candidate / receiver.STATIC_PAYLOAD_NAME).read_bytes())
        self.assertEqual(0o700, stat.S_IMODE(candidate.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(record_path.stat().st_mode))
        record = json.loads(record_path.read_bytes())
        self.assertEqual(receiver.STATIC_READBACK_SCHEMA, static_adopter.STATIC_ASSET_READBACK_SCHEMA)
        self.assertEqual(static_adopter.STATIC_ASSET_READBACK_SCHEMA, record["schema"])
        self.assertEqual(
            {
                "schema",
                "status",
                "campaign_id",
                "source_site",
                "consumer_site",
                "object",
                "transport",
                "age_decryption",
            },
            set(record),
        )
        self.assertNotIn("policy_sha256", record)
        self.assertEqual(report["object"], record["object"])
        self.assertEqual(report["object"], static_adopter._load_static_readback(path=record_path, campaign_id=self.campaign_id))
        self.assertEqual(
            [{"Bucket": self.policy.bucket, "Key": report["object"]["object_key"], "VersionId": report["object"]["version_id"]}],
            client.get_calls,
        )
        self.assertIn("-b" + receiver.binding.load_campaign_binding(self.binding).binding_sha256[:16], candidate.name)
        self.assertIn("-p" + receiver.policy_binding_sha256(self.policy)[:16], candidate.name)
        self.assertEqual(self.data_root, candidate.parents[2])
        self.assertEqual(True, result["capacity_preflight"]["same_filesystem"])

    def test_raw_image_receive_emits_exact_existing_image_readback_schema(self) -> None:
        report_path, report, plaintext, ciphertext = self._write_report(
            object_kind=receiver.contract.RAW_APP_IMAGE_OBJECT_KIND,
            object_id="raw-image-receive-fixture",
        )
        client = self._client_for_report(report, ciphertext)

        result = self._receive(report_path=report_path, client=client, payload=plaintext)

        candidate = Path(str(result["candidate_directory"]))
        record_path = Path(str(result["readback_record"]))
        self.assertEqual(plaintext, (candidate / receiver.SOURCE_IMAGE_PAYLOAD_NAME).read_bytes())
        record = json.loads(record_path.read_bytes())
        self.assertEqual(receiver.SOURCE_IMAGE_READBACK_SCHEMA, image_adopter.SOURCE_IMAGE_READBACK_SCHEMA)
        self.assertEqual(image_adopter.SOURCE_IMAGE_READBACK_SCHEMA, record["schema"])
        self.assertEqual(report["object"], image_adopter._load_source_image_readback(path=record_path, campaign_id=self.campaign_id))

    def test_prepare_rejects_mismatched_binding_recipient_and_non_supported_evidence(self) -> None:
        wrong_release, _report, _plaintext, _ciphertext = self._write_report(
            object_id="wrong-release", release_sha="f" * 40
        )
        with self.assertRaisesRegex(receiver.SourceObjectReceiveError, "canonical campaign release"):
            receiver.prepare_receive(
                controller_config=self.controller_config,
                campaign_binding_path=self.binding,
                upload_report_path=wrong_release,
            )

        wrong_recipients, _report, _plaintext, _ciphertext = self._write_report(
            object_id="wrong-recipients",
            recipients=(self.policy.controller_age_recipient,),
        )
        with self.assertRaises(receiver.SourceObjectReceiveError):
            receiver.prepare_receive(
                controller_config=self.controller_config,
                campaign_binding_path=self.binding,
                upload_report_path=wrong_recipients,
            )

        evidence, _report, _plaintext, _ciphertext = self._write_report(
            object_kind=receiver.contract.SOURCE_EVIDENCE_OBJECT_KIND,
            object_id="evidence-receive-fixture",
        )
        with self.assertRaisesRegex(receiver.SourceObjectReceiveError, "only the exact static or raw-app-image"):
            receiver.prepare_receive(
                controller_config=self.controller_config,
                campaign_binding_path=self.binding,
                upload_report_path=evidence,
            )

    def test_prepare_never_accepts_a_generic_publish_receipt_as_an_upload_report(self) -> None:
        generic_receipt = _private(
            self.inputs / "generic-publish-receipt.json",
            _canonical({"schema": receiver.contract.TRANSPORT_SCHEMA, "status": "published"}),
        )
        with self.assertRaisesRegex(receiver.SourceObjectReceiveError, "upload report is invalid"):
            receiver.prepare_receive(
                controller_config=self.controller_config,
                campaign_binding_path=self.binding,
                upload_report_path=generic_receipt,
            )

    def test_prepare_binds_canonical_policy_fields_and_main_blocks_before_client(self) -> None:
        report_path, _report, _plaintext, _ciphertext = self._write_report(object_id="policy-binding-fixture")
        changed_policy = receiver.transport.SourceTransportPolicy(
            **{**self.policy.__dict__, "prefix": "campaigns/different"}
        )
        changed_config = receiver.transport.ControllerS3Config(
            policy=changed_policy,
            credentials_file=self.credentials,
            campaign_id=self.campaign_id,
        )
        with self.assertRaisesRegex(receiver.SourceObjectReceiveError, "upload report is invalid"):
            receiver.prepare_receive(
                controller_config=changed_config,
                campaign_binding_path=self.binding,
                upload_report_path=report_path,
            )

        with (
            mock.patch.object(receiver.transport, "load_controller_config", return_value=changed_config),
            mock.patch.object(receiver.transport, "create_s3_client") as create_client,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(
                2,
                receiver.main(
                    [
                        "receive",
                        "--config",
                        str(self.root / "controller-config.json"),
                        "--campaign-binding",
                        str(self.binding),
                        "--upload-report",
                        str(report_path),
                    ]
                ),
            )
        create_client.assert_not_called()

    def test_prepare_requires_the_fixed_identity_recipient_before_any_s3_client(self) -> None:
        report_path, _report, _plaintext, _ciphertext = self._write_report(object_id="identity-policy-fixture")
        changed_policy = receiver.transport.SourceTransportPolicy(
            **{**self.policy.__dict__, "controller_age_recipient": recipient("e")}
        )
        changed_config = receiver.transport.ControllerS3Config(
            policy=changed_policy,
            credentials_file=self.credentials,
            campaign_id=self.campaign_id,
        )
        with self.assertRaisesRegex(receiver.SourceObjectReceiveError, "identity recipient does not match"):
            receiver.prepare_receive(
                controller_config=changed_config,
                campaign_binding_path=self.binding,
                upload_report_path=report_path,
            )
        with (
            mock.patch.object(receiver.transport, "load_controller_config", return_value=changed_config),
            mock.patch.object(receiver.transport, "create_s3_client") as create_client,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(
                2,
                receiver.main(
                    [
                        "receive",
                        "--config",
                        str(self.root / "controller-config.json"),
                        "--campaign-binding",
                        str(self.binding),
                        "--upload-report",
                        str(report_path),
                    ]
                ),
            )
        create_client.assert_not_called()

    def test_prepare_rejects_a_valid_other_campaign_config_before_identity_or_candidate_work(self) -> None:
        report_path, _report, _plaintext, _ciphertext = self._write_report(object_id="cross-campaign-config")
        other_campaign = "source-receiver-other-20260730"
        other_workspace = receiver.transport.contract.source_transport_workspace_for_campaign(other_campaign)
        other_workspace.mkdir(mode=0o700)
        other_workspace.chmod(0o700)
        other_config = receiver.transport.ControllerS3Config(
            policy=dataclasses.replace(self.policy, workspace=other_workspace),
            credentials_file=self.credentials,
            campaign_id=other_campaign,
        )
        with (
            mock.patch.object(receiver.identity_bootstrap, "load_verified_identity") as blocked_identity,
            self.assertRaisesRegex(
                receiver.SourceObjectReceiveError,
                "config does not bind the canonical campaign",
            ),
        ):
            receiver.prepare_receive(
                controller_config=other_config,
                campaign_binding_path=self.binding,
                upload_report_path=report_path,
            )
        blocked_identity.assert_not_called()
        self.assertEqual([], list(self.data_root.iterdir()))

    def test_prepare_requires_the_fixed_root_only_staging_data_root(self) -> None:
        report_path, _report, _plaintext, _ciphertext = self._write_report(object_id="unsafe-data-root")
        self.data_root.chmod(0o755)
        with self.assertRaisesRegex(receiver.SourceObjectReceiveError, "data root is unsafe"):
            receiver.prepare_receive(
                controller_config=self.controller_config,
                campaign_binding_path=self.binding,
                upload_report_path=report_path,
            )

    def test_read_only_staging_volume_blocks_before_s3_client_or_candidate_creation(self) -> None:
        report_path, _report, _plaintext, _ciphertext = self._write_report(object_id="read-only-staging-volume")
        stderr = io.StringIO()
        with (
            mock.patch.object(
                receiver.os,
                "statvfs",
                return_value=types.SimpleNamespace(f_flag=receiver.os.ST_RDONLY),
            ),
            mock.patch.object(receiver.transport, "load_controller_config", return_value=self.controller_config),
            mock.patch.object(receiver.transport, "create_s3_client") as create_client,
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(
                2,
                receiver.main(
                    [
                        "receive",
                        "--config",
                        str(self.root / "controller-config.json"),
                        "--campaign-binding",
                        str(self.binding),
                        "--upload-report",
                        str(report_path),
                    ]
                ),
            )
        self.assertIn("mounted read-only", stderr.getvalue())
        create_client.assert_not_called()
        self.assertEqual([], list(self.data_root.iterdir()))

    def test_candidate_creation_rechecks_the_staging_mount_before_mkdir(self) -> None:
        report_path, _report, _plaintext, _ciphertext = self._write_report(object_id="read-only-remount")
        plan = receiver.prepare_receive(
            controller_config=self.controller_config,
            campaign_binding_path=self.binding,
            upload_report_path=report_path,
        )
        with mock.patch.object(
            receiver.os,
            "statvfs",
            return_value=types.SimpleNamespace(f_flag=receiver.os.ST_RDONLY),
        ):
            with self.assertRaisesRegex(receiver.SourceObjectReceiveError, "mounted read-only"):
                receiver._create_candidate(plan)
        self.assertEqual([], list(self.data_root.iterdir()))

    def test_response_sse_version_metadata_ciphertext_and_plaintext_failures_preserve_candidates(self) -> None:
        cases = ("sse", "version", "metadata", "ciphertext", "plaintext")
        for case in cases:
            with self.subTest(case=case):
                report_path, report, plaintext, ciphertext = self._write_report(object_id="failure-" + case)
                client = self._client_for_report(report, ciphertext)
                if case == "sse":
                    client.returned_sse = "AES256"
                elif case == "version":
                    client.returned_version_id = "different-version"
                elif case == "metadata":
                    client.returned_metadata = {"transport-schema": "wrong"}
                elif case == "ciphertext":
                    client.returned_ciphertext = b"wrong ciphertext"
                    client.returned_content_length = len(client.returned_ciphertext)
                payload = b"wrong plaintext" if case == "plaintext" else plaintext
                plan = receiver.prepare_receive(
                    controller_config=self.controller_config,
                    campaign_binding_path=self.binding,
                    upload_report_path=report_path,
                )
                with self.assertRaises(receiver.SourceObjectReceiveError):
                    receiver.execute_receive(
                        client,
                        plan=plan,
                        decryptor=lambda plan, cipher, output: self._copy_decrypt(
                            plan, cipher, output, payload
                        ),
                    )
                self.assertTrue(plan.candidate_directory.is_dir())
                self.assertFalse((plan.candidate_directory / receiver.READBACK_RECORD_NAME).exists())

    def test_capacity_preflight_blocks_before_any_s3_call_on_the_fixed_staging_volume(self) -> None:
        report_path, report, plaintext, ciphertext = self._write_report(object_id="capacity-failure")
        client = self._client_for_report(report, ciphertext)
        plan = receiver.prepare_receive(
            controller_config=self.controller_config,
            campaign_binding_path=self.binding,
            upload_report_path=report_path,
        )
        required = (
            len(ciphertext)
            + len(plaintext)
            + receiver.READBACK_RECORD_RESERVE_BYTES
            + receiver.CAPACITY_MARGIN_BYTES
        )
        with mock.patch.object(
            receiver.shutil,
            "disk_usage",
            return_value=types.SimpleNamespace(free=required - 1),
        ):
            with self.assertRaisesRegex(receiver.SourceObjectReceiveError, "insufficient free space"):
                receiver.execute_receive(
                    client,
                    plan=plan,
                    decryptor=lambda plan, cipher, output: self._copy_decrypt(
                        plan, cipher, output, plaintext
                    ),
                )
        self.assertTrue(plan.candidate_directory.is_dir())
        self.assertEqual([], client.get_calls)
        self.assertEqual(0, client.bucket_calls)

    def test_capacity_preflight_rejects_a_candidate_on_a_different_filesystem(self) -> None:
        report_path, _report, _plaintext, _ciphertext = self._write_report(object_id="cross-filesystem")
        plan = receiver.prepare_receive(
            controller_config=self.controller_config,
            campaign_binding_path=self.binding,
            upload_report_path=report_path,
        )
        candidate = receiver._create_candidate(plan)

        def fake_stat(path: Path):
            return types.SimpleNamespace(st_dev=1 if path == self.data_root else 2)

        with self.assertRaisesRegex(receiver.SourceObjectReceiveError, "not on the fixed staging-volume filesystem"):
            receiver._capacity_preflight(
                plan=plan,
                candidate=candidate,
                disk_usage=lambda _path: types.SimpleNamespace(free=10**12),
                stat_path=fake_stat,
            )

    def test_failed_decrypt_preserves_the_candidate_and_never_reuses_it(self) -> None:
        report_path, report, _plaintext, ciphertext = self._write_report(object_id="decrypt-failure-fixture")
        client = self._client_for_report(report, ciphertext)
        plan = receiver.prepare_receive(
            controller_config=self.controller_config,
            campaign_binding_path=self.binding,
            upload_report_path=report_path,
        )

        with self.assertRaisesRegex(receiver.SourceObjectReceiveError, "age decryption"):
            receiver.execute_receive(
                client,
                plan=plan,
                decryptor=lambda *_args: (_ for _ in ()).throw(RuntimeError("fixture decrypt failure")),
            )
        self.assertTrue((plan.candidate_directory / receiver.CIPHERTEXT_NAME).is_file())
        self.assertFalse((plan.candidate_directory / receiver.READBACK_RECORD_NAME).exists())
        with self.assertRaisesRegex(receiver.SourceObjectReceiveError, "already exists"):
            receiver.execute_receive(client, plan=plan, decryptor=lambda *_args: None)

    def test_execute_revalidates_the_root_only_binding_and_report_before_s3(self) -> None:
        report_path, report, plaintext, ciphertext = self._write_report(object_id="stale-plan-fixture")
        client = self._client_for_report(report, ciphertext)
        plan = receiver.prepare_receive(
            controller_config=self.controller_config,
            campaign_binding_path=self.binding,
            upload_report_path=report_path,
        )
        stale = dataclasses.replace(plan, policy_sha256="0" * 64)
        with self.assertRaisesRegex(receiver.SourceObjectReceiveError, "changed after preflight"):
            receiver.execute_receive(
                client,
                plan=stale,
                decryptor=lambda plan, cipher, output: self._copy_decrypt(
                    plan, cipher, output, plaintext
                ),
            )
        self.assertEqual([], client.get_calls)

    def test_execute_refuses_a_forged_identity_path_before_s3(self) -> None:
        report_path, report, plaintext, ciphertext = self._write_report(object_id="forged-identity-path")
        client = self._client_for_report(report, ciphertext)
        plan = receiver.prepare_receive(
            controller_config=self.controller_config,
            campaign_binding_path=self.binding,
            upload_report_path=report_path,
        )
        forged = dataclasses.replace(plan, age_identity_file=self.root / "arbitrary.agekey")
        with self.assertRaisesRegex(receiver.SourceObjectReceiveError, "changed after preflight"):
            receiver.execute_receive(
                client,
                plan=forged,
                decryptor=lambda plan, cipher, output: self._copy_decrypt(
                    plan, cipher, output, plaintext
                ),
            )
        self.assertEqual([], client.get_calls)

    def test_execute_re_reads_the_fixed_identity_receipt_before_s3(self) -> None:
        report_path, _report, plaintext, ciphertext = self._write_report(object_id="stale-identity-receipt")
        client = self._client_for_report(_report, ciphertext)
        plan = receiver.prepare_receive(
            controller_config=self.controller_config,
            campaign_binding_path=self.binding,
            upload_report_path=report_path,
        )
        receipt = json.loads(self.identity_receipt.read_bytes())
        receipt["key_id"] = "0" * 64
        _private(self.identity_receipt, _canonical(receipt))
        with self.assertRaisesRegex(receiver.SourceObjectReceiveError, "identity or receipt is invalid"):
            receiver.execute_receive(
                client,
                plan=plan,
                decryptor=lambda plan, cipher, output: self._copy_decrypt(
                    plan, cipher, output, plaintext
                ),
            )
        self.assertEqual([], client.get_calls)

    def test_parser_has_no_generic_receipt_or_arbitrary_output_path(self) -> None:
        argv = [
            "receive",
            "--config",
            "/tmp/controller-config.json",
            "--campaign-binding",
            "/tmp/campaign/webapp-fi-source/campaign-binding.json",
            "--upload-report",
            "/tmp/upload-report.json",
        ]
        parsed = receiver._parser().parse_args(argv)
        self.assertFalse(hasattr(parsed, "publish_receipt"))
        self.assertFalse(hasattr(parsed, "candidate_directory"))
        self.assertFalse(hasattr(parsed, "output_directory"))
        self.assertFalse(hasattr(parsed, "data_root"))
        self.assertFalse(hasattr(parsed, "controller_age_identity"))
        self.assertFalse(hasattr(receiver, "run_age_decrypt"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            receiver._parser().parse_args(argv + ["--publish-receipt", "/tmp/generic.json"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            receiver._parser().parse_args(argv + ["--controller-age-identity", "/tmp/old.agekey"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
