from __future__ import annotations

import ast
from dataclasses import replace
import json
from pathlib import Path
import unittest

from core.append_only_sync_delta_batch import (
    DELTA_OBJECT_KIND,
    IMMUTABLE_RECEIPT_SCHEMA,
    IMMUTABLE_RECEIPT_STATUS,
    sha256_bytes,
)
from core.object_delta_source_transport_contract import (
    MAX_OBJECT_DELTA_CIPHERTEXT_OVERHEAD_BYTES,
    ObjectDeltaExactObjectVersionHistory,
    ObjectDeltaExactReadback,
    ObjectDeltaImmutableObjectDescriptor,
    ObjectDeltaSourceTransportAttempt,
    ObjectDeltaSourceTransportContractError,
    ObjectDeltaSourceTransportExpectation,
    ObjectDeltaSourceTransportPolicy,
    ObjectDeltaSourceTransportRequest,
    append_only_immutable_receipt_from_verified_source_transport_receipt,
    assess_object_delta_singleton_adopt_eligibility,
    build_verified_object_delta_source_transport_receipt,
    canonical_object_delta_source_transport_receipt_bytes,
    required_object_delta_source_upload_headers,
    required_object_delta_source_upload_metadata,
    resolve_object_delta_source_transport_route,
    strict_object_delta_source_transport_guarantees,
    validate_object_delta_exact_same_version_descriptor,
    validate_object_delta_source_transport_expectation,
    validate_object_delta_source_transport_policy,
    verify_object_delta_source_transport_receipt,
)
from core.object_delta_transport_binding import (
    OBJECT_DELTA_ENCRYPTION,
    OBJECT_DELTA_TRANSPORT_SCHEMA,
    ObjectDeltaTransportPolicy,
    derive_object_delta_object_key,
)


CAMPAIGN = "wa-ir-source-transport-20260731"
RELEASE = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
GENERATION = "fi-ir-source-transport-20260731"
PAYLOAD = b'{"items":[],"schema":"gold-trade-object-storage-append-only-sync-delta-payload-v1"}'
FI_RECIPIENT = "age1" + "a" * 30
IR_RECIPIENT = "age1" + "c" * 30


def policy(*, maximum_plaintext_bytes: int = 4096) -> ObjectDeltaSourceTransportPolicy:
    return ObjectDeltaSourceTransportPolicy(
        transport_policy=ObjectDeltaTransportPolicy(
            bucket="private-delta-bucket",
            prefix="campaigns/three-site",
            webapp_fi_age_recipient=FI_RECIPIENT,
            webapp_ir_age_recipient=IR_RECIPIENT,
        ),
        maximum_plaintext_bytes=maximum_plaintext_bytes,
    )


def request(
    *,
    first_sequence: int = 101,
    last_sequence: int = 102,
    payload_sha256: str | None = None,
) -> ObjectDeltaSourceTransportRequest:
    return ObjectDeltaSourceTransportRequest(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        stream_generation_id=GENERATION,
        first_sequence=first_sequence,
        last_sequence=last_sequence,
        payload_sha256=payload_sha256 or sha256_bytes(PAYLOAD),
    )


def expectation(
    *,
    plaintext_sha256: str | None = None,
    ciphertext_sha256: str = "d" * 64,
    ciphertext_bytes: int = 512,
) -> ObjectDeltaSourceTransportExpectation:
    return ObjectDeltaSourceTransportExpectation(
        plaintext_sha256=plaintext_sha256 or sha256_bytes(PAYLOAD),
        plaintext_bytes=len(PAYLOAD),
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=ciphertext_bytes,
    )


class ObjectDeltaSourceTransportContractTests(unittest.TestCase):
    def descriptor(self) -> ObjectDeltaImmutableObjectDescriptor:
        route = resolve_object_delta_source_transport_route(policy(), request())
        expected = expectation()
        return ObjectDeltaImmutableObjectDescriptor(
            object_key=route.object_key,
            version_id="version-20260731-01",
            ciphertext_sha256=expected.ciphertext_sha256,
            ciphertext_bytes=expected.ciphertext_bytes,
        )

    def readback(self, **changes: object) -> ObjectDeltaExactReadback:
        route = resolve_object_delta_source_transport_route(policy(), request())
        expected = expectation()
        values: dict[str, object] = {
            "object_key": route.object_key,
            "version_id": "version-20260731-01",
            "metadata": required_object_delta_source_upload_metadata(policy(), request(), expected),
            "ciphertext_sha256": expected.ciphertext_sha256,
            "ciphertext_bytes": expected.ciphertext_bytes,
            "provider_side_encryption": None,
        }
        values.update(changes)
        return ObjectDeltaExactReadback(**values)

    def test_route_uses_only_existing_delta_binding_and_pins_one_receiver(self) -> None:
        value = request()
        route = resolve_object_delta_source_transport_route(policy(), value)
        expected_key = derive_object_delta_object_key(
            policy().transport_policy,
            source_site=value.source_site,
            destination_site=value.destination_site,
            campaign_id=value.campaign_id,
            release_sha=value.release_sha,
            stream_generation_id=value.stream_generation_id,
            first_sequence=value.first_sequence,
            last_sequence=value.last_sequence,
            payload_sha256=value.payload_sha256,
        )

        self.assertEqual(expected_key, route.object_key)
        self.assertEqual(IR_RECIPIENT, route.destination_age_recipient)
        self.assertNotEqual(
            route.object_key,
            resolve_object_delta_source_transport_route(
                policy(),
                request(first_sequence=103, last_sequence=104),
            ).object_key,
        )

    def test_request_sequence_values_are_unbounded_but_range_is_bounded(self) -> None:
        high = request(first_sequence=100_001, last_sequence=100_002)
        route = resolve_object_delta_source_transport_route(policy(), high)
        self.assertIn("00000000000000100001-00000000000000100002", route.object_key)

        with self.assertRaisesRegex(ObjectDeltaSourceTransportContractError, "sequence range"):
            resolve_object_delta_source_transport_route(
                policy(),
                request(first_sequence=1, last_sequence=100_001),
            )

    def test_metadata_and_headers_are_exact_create_only_without_sse(self) -> None:
        metadata = required_object_delta_source_upload_metadata(policy(), request(), expectation())
        headers = required_object_delta_source_upload_headers(policy(), request(), expectation())

        self.assertEqual(
            {
                "transport-schema": OBJECT_DELTA_TRANSPORT_SCHEMA,
                "encryption": OBJECT_DELTA_ENCRYPTION,
                "ciphertext-sha256": "d" * 64,
                "source-site": "webapp_fi",
                "destination-site": "webapp_ir",
                "stream-generation-id": GENERATION,
            },
            metadata,
        )
        self.assertEqual("application/octet-stream", headers["content-type"])
        self.assertEqual("*", headers["if-none-match"])
        self.assertEqual(
            {"content-type", "if-none-match", *("x-amz-meta-" + name for name in metadata)},
            set(headers),
        )
        self.assertFalse(any("sse" in name.lower() for name in headers))
        self.assertEqual(
            {
                "encryption": OBJECT_DELTA_ENCRYPTION,
                "create_only": True,
                "private_bucket": True,
                "versioned_bucket": True,
                "provider_side_sse": False,
                "read_back_same_version_id": True,
                "controller_credentials_only": True,
            },
            strict_object_delta_source_transport_guarantees(),
        )

    def test_expectation_is_bound_to_the_payload_hash_and_size_limit(self) -> None:
        with self.assertRaisesRegex(ObjectDeltaSourceTransportContractError, "does not match"):
            validate_object_delta_source_transport_expectation(
                policy(),
                request(),
                expectation(plaintext_sha256="e" * 64),
            )

        oversized = ObjectDeltaSourceTransportExpectation(
            plaintext_sha256=sha256_bytes(PAYLOAD),
            plaintext_bytes=len(PAYLOAD),
            ciphertext_sha256="d" * 64,
            ciphertext_bytes=4096 + MAX_OBJECT_DELTA_CIPHERTEXT_OVERHEAD_BYTES + 1,
        )
        with self.assertRaisesRegex(ObjectDeltaSourceTransportContractError, "ciphertext bytes"):
            validate_object_delta_source_transport_expectation(policy(), request(), oversized)

    def test_exact_same_version_readback_is_required_before_receipt(self) -> None:
        route = resolve_object_delta_source_transport_route(policy(), request())
        expected = expectation()
        with self.assertRaises(TypeError):
            ObjectDeltaExactReadback(
                object_key=route.object_key,
                version_id="version-20260731-01",
                metadata=required_object_delta_source_upload_metadata(policy(), request(), expected),
                ciphertext_sha256=expected.ciphertext_sha256,
                ciphertext_bytes=expected.ciphertext_bytes,
            )

        descriptor = self.descriptor()
        verified = validate_object_delta_exact_same_version_descriptor(
            policy(),
            request(),
            expectation(),
            descriptor,
            self.readback(),
        )
        self.assertEqual(descriptor, verified)

        cases = (
            (
                replace(self.readback(), version_id="version-20260731-other"),
                "exact immutable VersionId",
            ),
            (
                replace(self.readback(), provider_side_encryption="AES256"),
                "provider-side encryption",
            ),
            (
                replace(self.readback(), metadata={"transport-schema": OBJECT_DELTA_TRANSPORT_SCHEMA}),
                "metadata",
            ),
            (
                replace(self.readback(), ciphertext_sha256="e" * 64),
                "ciphertext",
            ),
        )
        for readback, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(
                ObjectDeltaSourceTransportContractError,
                error,
            ):
                validate_object_delta_exact_same_version_descriptor(
                    policy(),
                    request(),
                    expectation(),
                    descriptor,
                    readback,
                )

    def test_url_free_receipt_round_trips_and_projects_to_existing_batch_receipt(self) -> None:
        receipt = build_verified_object_delta_source_transport_receipt(
            policy(),
            request(),
            expectation(),
            self.descriptor(),
            self.readback(),
        )
        payload = canonical_object_delta_source_transport_receipt_bytes(
            receipt,
            policy=policy(),
            request=request(),
            expectation=expectation(),
        )

        self.assertNotIn(b"://", payload.lower())
        self.assertNotIn(b"presigned", payload.lower())
        self.assertNotIn(b'"url"', payload.lower())
        self.assertEqual(
            self.descriptor(),
            verify_object_delta_source_transport_receipt(
                payload,
                policy=policy(),
                request=request(),
                expectation=expectation(),
            ),
        )
        self.assertEqual(
            {
                "schema": IMMUTABLE_RECEIPT_SCHEMA,
                "status": IMMUTABLE_RECEIPT_STATUS,
                "object_kind": DELTA_OBJECT_KIND,
                "object_key": self.descriptor().object_key,
                "version_id": self.descriptor().version_id,
                "ciphertext_sha256": self.descriptor().ciphertext_sha256,
                "ciphertext_bytes": self.descriptor().ciphertext_bytes,
            },
            append_only_immutable_receipt_from_verified_source_transport_receipt(
                payload,
                policy=policy(),
                request=request(),
                expectation=expectation(),
            ),
        )

    def test_receipt_rejects_url_protocol_and_checksum_tampering(self) -> None:
        receipt = build_verified_object_delta_source_transport_receipt(
            policy(),
            request(),
            expectation(),
            self.descriptor(),
            self.readback(),
        )
        raw_url = b'{"url":"https://example.invalid/forbidden"}\n'
        with self.assertRaisesRegex(ObjectDeltaSourceTransportContractError, "forbidden URL"):
            verify_object_delta_source_transport_receipt(
                raw_url,
                policy=policy(),
                request=request(),
                expectation=expectation(),
            )
        tampered = {**receipt, "receipt_sha256": "0" * 64}
        raw_tampered = json.dumps(
            tampered,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii") + b"\n"
        with self.assertRaisesRegex(ObjectDeltaSourceTransportContractError, "checksum"):
            verify_object_delta_source_transport_receipt(
                raw_tampered,
                policy=policy(),
                request=request(),
                expectation=expectation(),
            )

        deeply_nested = (b"[" * 12_000) + (b"]" * 12_000)
        self.assertLess(len(deeply_nested), 32 * 1024)
        with self.assertRaises(ObjectDeltaSourceTransportContractError):
            verify_object_delta_source_transport_receipt(
                deeply_nested,
                policy=policy(),
                request=request(),
                expectation=expectation(),
            )

    def test_singleton_adoption_requires_attempt_complete_history_and_exact_readback(self) -> None:
        descriptor = self.descriptor()
        attempt = ObjectDeltaSourceTransportAttempt(request=request(), expectation=expectation())
        history = ObjectDeltaExactObjectVersionHistory(
            object_key=descriptor.object_key,
            version_ids=(descriptor.version_id,),
            delete_marker_version_ids=(),
            latest_version_id=descriptor.version_id,
            listing_complete=True,
        )
        receipt = assess_object_delta_singleton_adopt_eligibility(
            policy(),
            attempt,
            history,
            self.readback(),
        )
        payload = canonical_object_delta_source_transport_receipt_bytes(
            receipt,
            policy=policy(),
            request=request(),
            expectation=expectation(),
        )
        self.assertEqual(descriptor, verify_object_delta_source_transport_receipt(
            payload,
            policy=policy(),
            request=request(),
            expectation=expectation(),
        ))

        invalid_histories = (
            replace(history, listing_complete=False),
            replace(history, version_ids=(descriptor.version_id, "version-other")),
            replace(history, delete_marker_version_ids=("delete-marker-1",)),
            replace(history, latest_version_id="version-other"),
        )
        for value in invalid_histories:
            with self.subTest(history=value), self.assertRaises(ObjectDeltaSourceTransportContractError):
                assess_object_delta_singleton_adopt_eligibility(
                    policy(),
                    attempt,
                    value,
                    self.readback(),
                )
        with self.assertRaisesRegex(ObjectDeltaSourceTransportContractError, "singleton version"):
            assess_object_delta_singleton_adopt_eligibility(
                policy(),
                attempt,
                history,
                replace(self.readback(), version_id="version-other"),
            )

    def test_policy_has_no_credential_material_and_rejects_non_controller_holder(self) -> None:
        self.assertFalse(
            {
                "credentials_file",
                "access_key",
                "secret_key",
                "session_token",
                "upload_url",
            }
            & set(ObjectDeltaSourceTransportPolicy.__dataclass_fields__)
        )
        invalid = ObjectDeltaSourceTransportPolicy(
            transport_policy=ObjectDeltaTransportPolicy(
                bucket="private-delta-bucket",
                prefix="campaigns/three-site",
                webapp_fi_age_recipient=FI_RECIPIENT,
                webapp_ir_age_recipient=IR_RECIPIENT,
                credential_holder="webapp_fi",
            )
        )
        with self.assertRaisesRegex(ObjectDeltaSourceTransportContractError, "policy"):
            validate_object_delta_source_transport_policy(invalid)


class ObjectDeltaSourceTransportContractStaticTests(unittest.TestCase):
    def test_contract_has_no_host_io_network_or_legacy_source_transport_import(self) -> None:
        path = Path(__file__).resolve().parents[1] / "core" / "object_delta_source_transport_contract.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        roots: set[str] = set()
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
                roots.add(node.module.split(".", 1)[0])
        forbidden_roots = {
            "boto3",
            "botocore",
            "http",
            "requests",
            "socket",
            "subprocess",
            "pathlib",
            "os",
            "tempfile",
            "urllib",
        }
        self.assertFalse(roots & forbidden_roots)
        self.assertFalse(any("webapp_fi_source_transport" in module for module in modules))
        self.assertFalse(
            [
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr
                in {"open", "read_bytes", "read_text", "write_bytes", "write_text", "unlink", "mkdir", "run"}
            ]
        )


if __name__ == "__main__":
    unittest.main()
