"""Pure-contract tests for the WebApp-FI source transport boundary."""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import quote


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "webapp_fi_source_transport_contract.py"
SPEC = importlib.util.spec_from_file_location("webapp_fi_source_transport_contract", MODULE_PATH)
assert SPEC and SPEC.loader
contract = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = contract
SPEC.loader.exec_module(contract)


def recipient(character: str) -> str:
    return "age1" + character * 40


class SourceTransportContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = contract.SourceTransportPolicy(
            endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
            region="ir-thr-at1",
            bucket="private-artifacts",
            prefix="campaigns/three-site",
            age_binary="/usr/bin/age",
            workspace=Path("/root/private-workspace"),
            controller_age_recipient=recipient("a"),
            webapp_fi_age_recipient=recipient("c"),
            webapp_ir_age_recipient=recipient("d"),
            maximum_plaintext_bytes=1024 * 1024,
        )

    def request(self, **changes: object) -> object:
        values: dict[str, object] = {
            "campaign_id": "source-transport-fixture-20260730",
            "release_sha": "1" * 40,
            "control_commit": "2" * 40,
            "control_tree": "3" * 40,
            "source_site": "webapp_fi",
            "destination_site": contract.STATIC_DESTINATION_SITE,
            "object_kind": contract.STATIC_OBJECT_KIND,
            "object_id": "static-20260730-01",
            "mode": contract.STATIC_MODE,
            "recipients": (
                self.policy.controller_age_recipient,
                self.policy.webapp_ir_age_recipient,
            ),
        }
        values.update(changes)
        return contract.SourceObjectRequest(**values)

    def descriptor(self, request: object | None = None) -> dict[str, object]:
        typed = request or self.request()
        assert isinstance(typed, contract.SourceObjectRequest)
        return {
            "object_key": contract.source_object_key(self.policy, typed),
            "version_id": "version-20260730-01",
            "ciphertext_sha256": "a" * 64,
            "ciphertext_bytes": 1234,
            "plaintext_sha256": "b" * 64,
            "plaintext_bytes": 1200,
        }

    def presigned_url(
        self,
        *,
        key: str,
        version_id: str | None = None,
        expires: str = "300",
        signed_headers: str | None = None,
        extra_query: tuple[str, ...] = (),
    ) -> str:
        if signed_headers is None:
            signed_headers = (
                "content-type;host;if-none-match;x-amz-meta-ciphertext-sha256;"
                "x-amz-meta-encryption;x-amz-meta-recipient-mode;x-amz-meta-transport-schema"
                if version_id is None
                else "host"
            )
        query = [
            "X-Amz-Algorithm=AWS4-HMAC-SHA256",
            "X-Amz-Credential=" + quote("FIXTURE/20260730/ir-thr-at1/s3/aws4_request", safe=""),
            "X-Amz-Date=20260730T010203Z",
            "X-Amz-Expires=" + expires,
            "X-Amz-SignedHeaders=" + quote(signed_headers, safe=""),
            "X-Amz-Signature=" + "a" * 64,
        ]
        query.extend(extra_query)
        if version_id is not None:
            query.insert(0, "versionId=" + quote(version_id, safe=""))
        return (
            "https://s3.ir-thr-at1.arvanstorage.ir/"
            + quote(self.policy.bucket, safe="")
            + "/"
            + quote(key, safe="/")
            + "?"
            + "&".join(query)
        )

    def test_contract_declares_no_host_or_network_capability(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".", 1)[0])
        forbidden = {
            "boto3",
            "botocore",
            "docker",
            "http",
            "paramiko",
            "requests",
            "socket",
            "subprocess",
            "tempfile",
            "urllib.request",
            "urllib.error",
            "manage_webapp_fi_source_transport",
            "manage_webapp_ir_snapshot",
            "verify_webapp_fi_source_provenance",
        }
        self.assertFalse(roots & forbidden)
        self.assertFalse(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr
                in {"open", "read_bytes", "read_text", "write_bytes", "write_text", "unlink", "mkdir", "run", "Popen"}
                for node in ast.walk(tree)
            )
        )

    def test_endpoint_region_and_campaign_workspace_are_deterministic(self) -> None:
        endpoint, region = contract.derive_region_from_endpoint("https://s3.ir-thr-at1.arvanstorage.ir/")
        self.assertEqual("https://s3.ir-thr-at1.arvanstorage.ir", endpoint)
        self.assertEqual("ir-thr-at1", region)
        self.assertEqual(
            Path("/srv/trading-bot-three-site-staging-data/webapp-fi-source/source-transport-fixture-20260730"),
            contract.source_transport_workspace_for_campaign("source-transport-fixture-20260730"),
        )
        self.assertEqual(100 * 1024 * 1024 * 1024, contract.MAXIMUM_PLAINTEXT_BYTES)

        for endpoint in (
            "http://s3.ir-thr-at1.arvanstorage.ir",
            "https://s3.ir-thr-at1.arvanstorage.ir:443",
            "https://s3.ir-thr-at1.arvanstorage.ir/not-an-origin",
            "https://s3.ir-thr-at1.arvanstorage.ir?query=not-allowed",
            "https://s3.ir-thr-at1.arvanstorage.ir.evil.example",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(
                contract.SourceTransportError, "canonical HTTPS Arvan S3 endpoint"
            ):
                contract.derive_region_from_endpoint(endpoint)

        with self.assertRaisesRegex(contract.SourceTransportError, "derived exactly"):
            contract.validate_policy(dataclasses.replace(self.policy, region="ir-foo-1"))

    def test_exact_five_direction_allowlist_and_dual_recipient_pin(self) -> None:
        self.assertEqual(5, len(contract.ALLOWED_DIRECTIONS))
        exact = contract.validate_request(self.policy, self.request())
        self.assertEqual(
            (self.policy.controller_age_recipient, self.policy.webapp_ir_age_recipient),
            exact,
        )
        cases = (
            (self.policy.webapp_ir_age_recipient, self.policy.controller_age_recipient),
            (self.policy.controller_age_recipient,),
            (
                self.policy.controller_age_recipient,
                self.policy.webapp_ir_age_recipient,
                self.policy.webapp_fi_age_recipient,
            ),
        )
        for recipients in cases:
            with self.subTest(recipients=recipients), self.assertRaisesRegex(
                contract.SourceTransportError, "static transport requires exactly"
            ):
                contract.validate_request(self.policy, self.request(recipients=recipients))

        with self.assertRaisesRegex(contract.SourceTransportError, "direction, object kind"):
            contract.validate_request(self.policy, self.request(object_kind="not-allowed"))

    def test_all_single_direction_recipient_pins(self) -> None:
        cases = (
            ("bot_fi", "webapp_fi", contract.BOOTSTRAP_OBJECT_KIND, self.policy.webapp_fi_age_recipient),
            ("controller", "webapp_fi", contract.STATIC_PROVENANCE_OBJECT_KIND, self.policy.webapp_fi_age_recipient),
            ("webapp_fi", "controller", contract.RAW_APP_IMAGE_OBJECT_KIND, self.policy.controller_age_recipient),
            ("webapp_fi", "controller", contract.SOURCE_EVIDENCE_OBJECT_KIND, self.policy.controller_age_recipient),
        )
        for source, destination, kind, expected_recipient in cases:
            with self.subTest(source=source, destination=destination, kind=kind):
                request = self.request(
                    source_site=source,
                    destination_site=destination,
                    object_kind=kind,
                    mode=contract.SINGLE_MODE,
                    recipients=(expected_recipient,),
                )
                self.assertEqual((expected_recipient,), contract.validate_request(self.policy, request))
                with self.assertRaisesRegex(contract.SourceTransportError, "single transport requires exactly"):
                    contract.validate_request(
                        self.policy,
                        dataclasses.replace(request, recipients=(self.policy.webapp_ir_age_recipient,)),
                    )

    def test_key_descriptor_and_receipt_bind_release_and_control(self) -> None:
        request = self.request()
        key = contract.source_object_key(self.policy, request)
        self.assertIn("/" + request.release_sha + "/", key)
        self.assertIn("/" + request.control_commit + "/", key)
        self.assertIn("/" + request.control_tree + "/", key)
        self.assertNotEqual(key, contract.source_object_key(self.policy, dataclasses.replace(request, release_sha="a" * 40)))
        self.assertNotEqual(key, contract.source_object_key(self.policy, dataclasses.replace(request, control_tree="c" * 40)))

        receipt = contract.build_publish_receipt(
            config=self.policy,
            request=request,
            descriptor=self.descriptor(request),
        )
        payload = contract.canonical_json_bytes(receipt) + b"\n"
        self.assertEqual(receipt, contract.verify_publish_receipt(config=self.policy, payload=payload))
        forged = dict(receipt)
        forged["object"] = {**forged["object"], "object_key": key.replace(request.release_sha, "a" * 40)}
        forged_payload = contract.canonical_json_bytes(forged) + b"\n"
        with self.assertRaisesRegex(contract.SourceTransportError, "object key is not bound"):
            contract.verify_publish_receipt(config=self.policy, payload=forged_payload)

        extra = {**receipt, "unexpected": True}
        with self.assertRaisesRegex(contract.SourceTransportError, "receipt is unsupported"):
            contract.verify_publish_receipt(
                config=self.policy,
                payload=contract.canonical_json_bytes(extra) + b"\n",
            )

        persisted_url = json.dumps({"url": "https://bad.example"}, sort_keys=True).encode("ascii") + b"\n"
        with self.assertRaises(contract.SourceTransportError):
            contract.verify_publish_receipt(config=self.policy, payload=persisted_url)

    def test_presigned_urls_require_short_expiry_and_exact_version_binding(self) -> None:
        request = self.request()
        key = contract.source_object_key(self.policy, request)
        put = self.presigned_url(key=key)
        self.assertEqual(
            put,
            contract.require_create_only_presigned_put_url(put, policy=self.policy, object_key=key),
        )
        get = self.presigned_url(key=key, version_id="version-20260730-01")
        self.assertEqual(
            get,
            contract.require_version_bound_presigned_get_url(
                get,
                policy=self.policy,
                object_key=key,
                version_id="version-20260730-01",
            ),
        )
        for expiry in ("0", "901", "not-a-number"):
            with self.subTest(expiry=expiry), self.assertRaisesRegex(contract.SourceTransportError, "expiry"):
                contract.require_create_only_presigned_put_url(
                    self.presigned_url(key=key, expires=expiry),
                    policy=self.policy,
                    object_key=key,
                )
        with self.assertRaisesRegex(contract.SourceTransportError, "signing time"):
            contract.require_create_only_presigned_put_url(
                self.presigned_url(key=key).replace("20260730T010203Z", "20261330T010203Z"),
                policy=self.policy,
                object_key=key,
            )
        with self.assertRaisesRegex(contract.SourceTransportError, "must not target"):
            contract.require_create_only_presigned_put_url(
                self.presigned_url(
                    key=key,
                    version_id="old-version",
                    signed_headers=(
                        "content-type;host;if-none-match;x-amz-meta-ciphertext-sha256;"
                        "x-amz-meta-encryption;x-amz-meta-recipient-mode;x-amz-meta-transport-schema"
                    ),
                ),
                policy=self.policy,
                object_key=key,
            )
        with self.assertRaisesRegex(contract.SourceTransportError, "must bind exactly"):
            contract.require_version_bound_presigned_get_url(
                self.presigned_url(key=key, version_id="other-version"),
                policy=self.policy,
                object_key=key,
                version_id="version-20260730-01",
            )

    def test_presigned_urls_reject_header_supersets_missing_headers_and_extra_query(self) -> None:
        key = contract.source_object_key(self.policy, self.request())
        exact_put = (
            "content-type;host;if-none-match;x-amz-meta-ciphertext-sha256;"
            "x-amz-meta-encryption;x-amz-meta-recipient-mode;x-amz-meta-transport-schema"
        )
        cases = (
            ("missing-put-header", "host", (), "signed headers"),
            (
                "sse-header",
                exact_put + ";x-amz-server-side-encryption",
                (),
                "signed headers",
            ),
            ("extra-query", exact_put, ("x-id=PutObject",), "exact supported SigV4"),
        )
        for name, signed_headers, extra_query, message in cases:
            with self.subTest(case=name), self.assertRaisesRegex(contract.SourceTransportError, message):
                contract.require_create_only_presigned_put_url(
                    self.presigned_url(key=key, signed_headers=signed_headers, extra_query=extra_query),
                    policy=self.policy,
                    object_key=key,
                )

        with self.assertRaisesRegex(contract.SourceTransportError, "signed headers"):
            contract.require_version_bound_presigned_get_url(
                self.presigned_url(
                    key=key,
                    version_id="version-20260730-01",
                    signed_headers=exact_put,
                ),
                policy=self.policy,
                object_key=key,
                version_id="version-20260730-01",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
