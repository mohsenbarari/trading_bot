"""Focused local tests for the controller static transport-receipt finalizer."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
from pathlib import Path
import stat
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    return module


finalizer = _load(
    "test_finalize_webapp_fi_static_transport_receipt",
    "finalize_webapp_fi_static_transport_receipt.py",
)
renderer = _load(
    "test_finalize_webapp_fi_static_renderer",
    "render_webapp_ir_static_receive.py",
)
source_receiver_tests = _load(
    "test_finalize_webapp_fi_static_source_receiver_fixture",
    "../tests/test_receive_webapp_fi_source_object.py",
)


class StaticTransportReceiptFinalizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = source_receiver_tests.SourceObjectReceiverTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.policy = finalizer.receiver.transport.SourceTransportPolicy(
            endpoint=self.fixture.policy.endpoint,
            region=self.fixture.policy.region,
            bucket=self.fixture.policy.bucket,
            prefix=self.fixture.policy.prefix,
            age_binary=self.fixture.policy.age_binary,
            workspace=self.fixture.workspace,
            controller_age_recipient=self.fixture.policy.controller_age_recipient,
            webapp_fi_age_recipient=self.fixture.policy.webapp_fi_age_recipient,
            webapp_ir_age_recipient=self.fixture.policy.webapp_ir_age_recipient,
            maximum_plaintext_bytes=self.fixture.policy.maximum_plaintext_bytes,
        )
        self.controller_config = finalizer.receiver.transport.ControllerS3Config(
            policy=self.policy,
            credentials_file=self.fixture.credentials,
            campaign_id=self.fixture.campaign_id,
        )
        patches = (
            mock.patch.object(
                finalizer.receiver.transport.contract,
                "SOURCE_TRANSPORT_WORKSPACE_ROOT",
                self.fixture.workspace_root,
            ),
            mock.patch.object(
                finalizer.receiver,
                "CONTROLLER_SOURCE_RECEIVE_ROOT",
                self.fixture.data_root,
            ),
            mock.patch.object(
                finalizer.receiver.identity_bootstrap,
                "CAMPAIGNS_ROOT",
                self.fixture.root,
            ),
            mock.patch.object(
                finalizer.receiver.identity_bootstrap,
                "AGE_KEYGEN_BINARY",
                self.fixture.test_age_keygen,
            ),
            mock.patch.object(
                finalizer.receiver.identity_bootstrap,
                "derive_recipient",
                return_value=self.policy.controller_age_recipient,
            ),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _receive_static(self, *, object_id: str = "static-finalizer-fixture"):
        report_path, report, plaintext, ciphertext = self.fixture._write_report(object_id=object_id)
        result = self.fixture._receive(
            report_path=report_path,
            client=self.fixture._client_for_report(report, ciphertext),
            payload=plaintext,
        )
        return report_path, report, plaintext, ciphertext, result

    def _prepare(self, report_path: Path):
        return finalizer.prepare_static_transport_receipt(
            controller_config=self.controller_config,
            campaign_binding_path=self.fixture.binding,
            upload_report_path=report_path,
        )

    def test_plan_then_finalize_creates_renderer_compatible_create_only_receipt(self) -> None:
        report_path, report, _plaintext, _ciphertext, received = self._receive_static()

        plan = self._prepare(report_path)

        self.assertEqual(Path(str(received["candidate_directory"])), plan.receipt_path.parent)
        self.assertEqual(finalizer.TRANSPORT_RECEIPT_NAME, plan.receipt_path.name)
        self.assertFalse(plan.receipt_path.exists())
        self.assertEqual(report["object"], plan.receipt["object"])
        self.assertEqual(
            [self.policy.controller_age_recipient, self.policy.webapp_ir_age_recipient],
            plan.receipt["recipients"],
        )

        result = finalizer.finalize_static_transport_receipt(plan=plan)

        self.assertEqual("finalized", result["status"])
        self.assertEqual(0o600, stat.S_IMODE(plan.receipt_path.stat().st_mode))
        payload = plan.receipt_path.read_bytes()
        self.assertNotIn(b"://", payload)
        self.assertEqual(
            plan.receipt,
            finalizer.receiver.transport.verify_publish_receipt(
                config=self.policy,
                payload=payload,
            ),
        )
        renderer_policy = renderer.transport.SourceTransportPolicy(
            endpoint=self.policy.endpoint,
            region=self.policy.region,
            bucket=self.policy.bucket,
            prefix=self.policy.prefix,
            age_binary=self.policy.age_binary,
            workspace=self.policy.workspace,
            controller_age_recipient=self.policy.controller_age_recipient,
            webapp_fi_age_recipient=self.policy.webapp_fi_age_recipient,
            webapp_ir_age_recipient=self.policy.webapp_ir_age_recipient,
            maximum_plaintext_bytes=self.policy.maximum_plaintext_bytes,
        )
        rendered = renderer._verify_generic_static_receipt(payload=payload, policy=renderer_policy)
        self.assertEqual(plan.receipt, rendered)
        with self.assertRaisesRegex(
            finalizer.StaticTransportReceiptFinalizationError,
            "already exists and will not be reused",
        ):
            self._prepare(report_path)

    def test_tampered_readback_candidate_never_produces_a_generic_receipt(self) -> None:
        report_path, _report, _plaintext, _ciphertext, received = self._receive_static(
            object_id="static-finalizer-tamper"
        )
        candidate = Path(str(received["candidate_directory"]))
        archive = candidate / finalizer.receiver.STATIC_PAYLOAD_NAME
        archive.write_bytes(b"different static archive\n")
        archive.chmod(0o600)

        with self.assertRaisesRegex(
            finalizer.StaticTransportReceiptFinalizationError,
            "decrypted archive does not match",
        ):
            self._prepare(report_path)
        self.assertFalse((candidate / finalizer.TRANSPORT_RECEIPT_NAME).exists())

    def test_nonstatic_route_and_extra_candidate_artifact_are_rejected(self) -> None:
        raw_report, raw_value, plaintext, ciphertext = self.fixture._write_report(
            object_kind=finalizer.receiver.contract.RAW_APP_IMAGE_OBJECT_KIND,
            object_id="raw-finalizer-fixture",
        )
        self.fixture._receive(
            report_path=raw_report,
            client=self.fixture._client_for_report(raw_value, ciphertext),
            payload=plaintext,
        )
        with self.assertRaisesRegex(
            finalizer.StaticTransportReceiptFinalizationError,
            "exact static dual-recipient route",
        ):
            self._prepare(raw_report)

        static_report, _report, _plaintext, _ciphertext, received = self._receive_static(
            object_id="static-finalizer-extra"
        )
        candidate = Path(str(received["candidate_directory"]))
        extra = candidate / "unexpected.json"
        extra.write_text("{}\n", encoding="ascii")
        extra.chmod(0o600)
        with self.assertRaisesRegex(
            finalizer.StaticTransportReceiptFinalizationError,
            "unsupported artifact set",
        ):
            self._prepare(static_report)
        self.assertFalse((candidate / finalizer.TRANSPORT_RECEIPT_NAME).exists())

    def test_prepare_does_not_construct_an_s3_client(self) -> None:
        report_path, _report, _plaintext, _ciphertext, _received = self._receive_static(
            object_id="static-finalizer-local-only"
        )
        with mock.patch.object(finalizer.receiver.transport, "create_s3_client") as create_client:
            plan = self._prepare(report_path)
        self.assertEqual("static", plan.receipt["object_kind"])
        create_client.assert_not_called()

    def test_other_campaign_config_is_rejected_without_creating_a_static_receipt(self) -> None:
        report_path, _report, _plaintext, _ciphertext = self.fixture._write_report(
            object_id="static-finalizer-cross-campaign"
        )
        other_campaign = "source-receiver-finalizer-other-20260730"
        other_workspace = finalizer.receiver.transport.contract.source_transport_workspace_for_campaign(
            other_campaign
        )
        other_workspace.mkdir(mode=0o700)
        other_config = finalizer.receiver.transport.ControllerS3Config(
            policy=dataclasses.replace(self.policy, workspace=other_workspace),
            credentials_file=self.fixture.credentials,
            campaign_id=other_campaign,
        )
        with (
            mock.patch.object(finalizer.receiver.transport, "build_publish_receipt") as blocked_receipt,
            self.assertRaisesRegex(
                finalizer.StaticTransportReceiptFinalizationError,
                "canonical campaign binding, FI upload report, or controller receive identity is invalid",
            ),
        ):
            finalizer.prepare_static_transport_receipt(
                controller_config=other_config,
                campaign_binding_path=self.fixture.binding,
                upload_report_path=report_path,
            )
        blocked_receipt.assert_not_called()
        self.assertEqual([], list(self.fixture.data_root.iterdir()))

    def test_renderer_accepts_contract_valid_opaque_x_amz_object_id(self) -> None:
        report_path, _report, _plaintext, _ciphertext, _received = self._receive_static(
            object_id="x-amz-static-fixture"
        )
        plan = self._prepare(report_path)
        finalizer.finalize_static_transport_receipt(plan=plan)
        policy = renderer.transport.SourceTransportPolicy(
            endpoint=self.policy.endpoint,
            region=self.policy.region,
            bucket=self.policy.bucket,
            prefix=self.policy.prefix,
            age_binary=self.policy.age_binary,
            workspace=self.policy.workspace,
            controller_age_recipient=self.policy.controller_age_recipient,
            webapp_fi_age_recipient=self.policy.webapp_fi_age_recipient,
            webapp_ir_age_recipient=self.policy.webapp_ir_age_recipient,
            maximum_plaintext_bytes=self.policy.maximum_plaintext_bytes,
        )
        self.assertEqual(
            plan.receipt,
            renderer._verify_generic_static_receipt(
                payload=plan.receipt_path.read_bytes(),
                policy=policy,
            ),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
