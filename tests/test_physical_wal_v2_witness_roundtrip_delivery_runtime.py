from __future__ import annotations

import ast
from datetime import timedelta
import hashlib
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import physical_wal_v2_witness_roundtrip_contract as roundtrip
from core import physical_wal_v2_witness_roundtrip_delivery_contract as delivery
from core import physical_wal_v2_witness_roundtrip_delivery_runtime as runtime
from tests import test_physical_wal_v2_witness_roundtrip_contract as roundtrip_contract_tests
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW


class _FixedPublisher:
    def __init__(self, *, fail: bool = False, mutate_receipt=None) -> None:
        self.fail = fail
        self.mutate_receipt = mutate_receipt
        self.calls: list[dict[str, object]] = []

    def _create(self, **kwargs: object):
        self.calls.append(dict(kwargs))
        if self.fail:
            raise RuntimeError("adapter-failure")
        receipt = runtime.PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt(
            object_key=kwargs["object_key"],
            object_version_id="version-000001",
            content_sha256=kwargs["content_sha256"],
            content_bytes=kwargs["content_bytes"],
            retained_until=kwargs["retained_until"],
            create_only=True,
            immutable=True,
        )
        return receipt if self.mutate_receipt is None else self.mutate_receipt(receipt)

    def create_fi_to_witness_delivery(self, **kwargs: object):
        return self._create(**kwargs)

    def create_witness_to_ir_delivery(self, **kwargs: object):
        return self._create(**kwargs)

    def create_ir_to_witness_delivery(self, **kwargs: object):
        return self._create(**kwargs)

    def create_witness_to_fi_delivery(self, **kwargs: object):
        return self._create(**kwargs)


class _FixedScanner:
    def __init__(
        self,
        locators: tuple[runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator, ...],
        content: dict[tuple[str, str], runtime.PhysicalWalV2WitnessRoundtripDeliveryContent],
    ) -> None:
        self.locators = locators
        self.content = content
        self.list_calls = 0
        self.read_calls: list[tuple[str, str]] = []

    def _list(self):
        self.list_calls += 1
        return self.locators

    def _read(self, *, object_key: str, object_version_id: str):
        self.read_calls.append((object_key, object_version_id))
        return self.content[(object_key, object_version_id)]

    def list_fi_to_witness_delivery_locators(self):
        return self._list()

    def read_fi_to_witness_delivery_exact(self, *, object_key: str, object_version_id: str):
        return self._read(object_key=object_key, object_version_id=object_version_id)

    def list_witness_to_ir_delivery_locators(self):
        return self._list()

    def read_witness_to_ir_delivery_exact(self, *, object_key: str, object_version_id: str):
        return self._read(object_key=object_key, object_version_id=object_version_id)

    def list_ir_to_witness_delivery_locators(self):
        return self._list()

    def read_ir_to_witness_delivery_exact(self, *, object_key: str, object_version_id: str):
        return self._read(object_key=object_key, object_version_id=object_version_id)

    def list_witness_to_fi_delivery_locators(self):
        return self._list()

    def read_witness_to_fi_delivery_exact(self, *, object_key: str, object_version_id: str):
        return self._read(object_key=object_key, object_version_id=object_version_id)


class PhysicalWalV2WitnessRoundtripDeliveryRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = roundtrip_contract_tests.PhysicalWalV2WitnessRoundtripContractTests(
            "runTest"
        )
        self.fixture.setUp()
        recovery_export = self.fixture._recovery_export()
        certificate = self.fixture._certificate(recovery_export)
        envelope = self.fixture._source_envelope(certificate)
        assertion, _issued = self.fixture._assertion(envelope)
        attestation = self.fixture._attestation(assertion)
        self.certificate = (
            roundtrip.verify_physical_wal_v2_witness_context_certificate(
                certificate,
                config=self.fixture.config,
                now=NOW,
            ).canonical_certificate
        )
        self.envelope = roundtrip.verify_physical_wal_v2_witness_source_envelope(
            envelope,
            config=self.fixture.config,
            now=NOW,
        ).canonical_envelope
        self.assertion = roundtrip.verify_physical_wal_v2_witness_ir_durable_assertion(
            assertion,
            config=self.fixture.config,
            now=NOW,
        ).canonical_assertion
        self.attestation = roundtrip.verify_physical_wal_v2_witness_roundtrip_attestation(
            attestation,
            config=self.fixture.config,
            now=NOW,
        ).canonical_attestation
        self.binding = delivery.build_physical_wal_v2_witness_roundtrip_delivery_binding(
            context_certificate=self.certificate,
            roundtrip_config=self.fixture.config,
            now=NOW,
        )
        self.deliveries = {
            "fi-to-witness": delivery.build_physical_wal_v2_witness_fi_to_witness_delivery(
                context_certificate=self.certificate,
                source_envelope=self.envelope,
                config=self._delivery_policy("fi-to-witness"),
                now=NOW,
            ),
            "witness-to-ir": delivery.build_physical_wal_v2_witness_witness_to_ir_delivery(
                context_certificate=self.certificate,
                source_envelope=self.envelope,
                config=self._delivery_policy("witness-to-ir"),
                now=NOW,
            ),
            "ir-to-witness": delivery.build_physical_wal_v2_witness_ir_to_witness_delivery(
                ir_durable_assertion=self.assertion,
                config=self._delivery_policy("ir-to-witness"),
                now=NOW,
            ),
            "witness-to-fi": delivery.build_physical_wal_v2_witness_witness_to_fi_delivery(
                roundtrip_attestation=self.attestation,
                config=self._delivery_policy("witness-to-fi"),
                now=NOW,
            ),
        }

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def _delivery_policy(self, mailbox: str):
        return delivery.PhysicalWalV2WitnessRoundtripDeliveryConfig(
            roundtrip_config=self.fixture.config,
            binding=self.binding,
            receiver_mailbox=mailbox,
            enabled=True,
        )

    def _runtime_config(self, root: Path, *, local_role: str, mailbox: str):
        return runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeConfig(
            state_root=root,
            delivery_config=self._delivery_policy(mailbox),
            local_role=local_role,
            enabled=True,
            maximum_records=16,
        )

    @staticmethod
    def _locator_and_content(
        mailbox: str,
        packet: bytes,
        *,
        version: str = "version-000001",
        content_version: str | None = None,
        key: str | None = None,
        retention=NOW + timedelta(seconds=90),
    ):
        digest = hashlib.sha256(packet).hexdigest()
        object_key = key or runtime._object_key(mailbox, digest)
        locator = runtime.PhysicalWalV2WitnessRoundtripDeliveryImmutableLocator(
            object_key=object_key,
            object_version_id=version,
            content_sha256=digest,
            content_bytes=len(packet),
            retained_until=retention,
            immutable=True,
        )
        content = runtime.PhysicalWalV2WitnessRoundtripDeliveryContent(
            object_key=object_key,
            object_version_id=content_version or version,
            content_sha256=digest,
            content_bytes=len(packet),
            retained_until=retention,
            immutable=True,
            canonical_delivery=packet,
        )
        return locator, content

    def _open(self, root: Path, *, local_role: str, mailbox: str):
        with patch.object(runtime, "_host_now", return_value=NOW):
            return runtime.open_physical_wal_v2_witness_roundtrip_delivery_runtime(
                config=self._runtime_config(root, local_role=local_role, mailbox=mailbox)
            )

    def test_all_eight_role_boundaries_accept_only_their_fixed_four_hops(self) -> None:
        cases = (
            (
                "fi-to-witness",
                "fi-writer-source-outbox",
                "witness-fi-ingress",
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery,
                runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery,
            ),
            (
                "witness-to-ir",
                "witness-ir-egress",
                "ir-standby-ack-inbox",
                runtime.publish_physical_wal_v2_witness_witness_to_ir_delivery,
                runtime.consume_physical_wal_v2_witness_witness_to_ir_delivery,
            ),
            (
                "ir-to-witness",
                "ir-durable-ack-outbox",
                "witness-ir-ingress",
                runtime.publish_physical_wal_v2_witness_ir_to_witness_delivery,
                runtime.consume_physical_wal_v2_witness_ir_to_witness_delivery,
            ),
            (
                "witness-to-fi",
                "witness-fi-egress",
                "fi-writer-ack-inbox",
                runtime.publish_physical_wal_v2_witness_witness_to_fi_delivery,
                runtime.consume_physical_wal_v2_witness_witness_to_fi_delivery,
            ),
        )
        for mailbox, outbound_role, inbound_role, publish, consume in cases:
            with self.subTest(mailbox=mailbox), tempfile.TemporaryDirectory() as outbound_path, tempfile.TemporaryDirectory() as inbound_path:
                packet = self.deliveries[mailbox]
                outbound = self._open(Path(outbound_path), local_role=outbound_role, mailbox=mailbox)
                publisher = _FixedPublisher()
                with patch.object(runtime, "_host_now", return_value=NOW):
                    publication = publish(outbound, packet, publisher=publisher)
                self.assertEqual("published", publication.status)
                self.assertEqual(1, len(publisher.calls))
                self.assertTrue(publication.object_key.startswith(runtime._object_prefix(mailbox)))
                locator, content = self._locator_and_content(
                    mailbox,
                    packet,
                    version=publication.object_version_id,
                    retention=publication.retained_until,
                )
                scanner = _FixedScanner(
                    (locator,), {(locator.object_key, locator.object_version_id): content}
                )
                inbound = self._open(Path(inbound_path), local_role=inbound_role, mailbox=mailbox)
                with patch.object(runtime, "_host_now", return_value=NOW):
                    consumed = consume(inbound, scanner=scanner)
                self.assertEqual(1, len(consumed))
                self.assertEqual("consumed", consumed[0].status)
                self.assertEqual(publication.delivery_sha256, consumed[0].delivery_sha256)
                self.assertEqual(1, scanner.list_calls)
                self.assertEqual([(locator.object_key, locator.object_version_id)], scanner.read_calls)

    def test_role_and_policy_mismatches_are_rejected_before_adapter_use(self) -> None:
        with tempfile.TemporaryDirectory() as path:
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_POLICY_INVALID",
            ):
                runtime.open_physical_wal_v2_witness_roundtrip_delivery_runtime(
                    config=self._runtime_config(
                        Path(path),
                        local_role="fi-writer-source-outbox",
                        mailbox="witness-to-ir",
                    )
                )
        with tempfile.TemporaryDirectory() as path:
            inbound = self._open(
                Path(path), local_role="witness-fi-ingress", mailbox="fi-to-witness"
            )
            publisher = _FixedPublisher()
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_HANDLE_INVALID",
            ):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    inbound,
                    self.deliveries["fi-to-witness"],
                    publisher=publisher,
                )
            self.assertEqual([], publisher.calls)

    def test_publish_duplicate_retry_and_post_reservation_failure_are_fail_closed(self) -> None:
        packet = self.deliveries["fi-to-witness"]
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            handle = self._open(root, local_role="fi-writer-source-outbox", mailbox="fi-to-witness")
            publisher = _FixedPublisher()
            with patch.object(runtime, "_host_now", return_value=NOW):
                first = runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, packet, publisher=publisher
                )
                retry = runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, packet, publisher=publisher
                )
            self.assertFalse(first.idempotent)
            self.assertTrue(retry.idempotent)
            self.assertEqual(1, len(publisher.calls))
            reopened = self._open(root, local_role="fi-writer-source-outbox", mailbox="fi-to-witness")
            with patch.object(runtime, "_host_now", return_value=NOW):
                persisted_retry = runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    reopened, packet, publisher=publisher
                )
            self.assertTrue(persisted_retry.idempotent)
            self.assertEqual(1, len(publisher.calls))

        with tempfile.TemporaryDirectory() as path:
            handle = self._open(
                Path(path), local_role="fi-writer-source-outbox", mailbox="fi-to-witness"
            )
            failing = _FixedPublisher(fail=True)
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_PUBLISHER_FAILED",
            ):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, packet, publisher=failing
                )
            self.assertEqual(1, len(failing.calls))
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_PUBLISH_INDETERMINATE",
            ):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, packet, publisher=failing
                )
            self.assertEqual(1, len(failing.calls))

        with tempfile.TemporaryDirectory() as path:
            handle = self._open(
                Path(path), local_role="fi-writer-source-outbox", mailbox="fi-to-witness"
            )
            bad_receipt = _FixedPublisher(
                mutate_receipt=lambda receipt: runtime.PhysicalWalV2WitnessRoundtripDeliveryCreateOnlyReceipt(
                    object_key=receipt.object_key + ".foreign",
                    object_version_id=receipt.object_version_id,
                    content_sha256=receipt.content_sha256,
                    content_bytes=receipt.content_bytes,
                    retained_until=receipt.retained_until,
                    create_only=receipt.create_only,
                    immutable=receipt.immutable,
                )
            )
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_PUBLISH_RECEIPT_INVALID",
            ):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, packet, publisher=bad_receipt
                )
            self.assertEqual(1, len(bad_receipt.calls))
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_PUBLISH_INDETERMINATE",
            ):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, packet, publisher=bad_receipt
                )
            self.assertEqual(1, len(bad_receipt.calls))

    def test_inbound_exact_version_content_substitution_and_tampered_wire_fail_before_consume(self) -> None:
        packet = self.deliveries["fi-to-witness"]
        with tempfile.TemporaryDirectory() as path:
            handle = self._open(Path(path), local_role="witness-fi-ingress", mailbox="fi-to-witness")
            wrong_key = runtime._object_key("witness-to-ir", hashlib.sha256(packet).hexdigest())
            locator, content = self._locator_and_content("fi-to-witness", packet, key=wrong_key)
            scanner = _FixedScanner(
                (locator,), {(locator.object_key, locator.object_version_id): content}
            )
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_LOCATOR_PREFIX_INVALID",
            ):
                runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, scanner=scanner
                )
            self.assertEqual([], scanner.read_calls)

        with tempfile.TemporaryDirectory() as path:
            handle = self._open(Path(path), local_role="witness-fi-ingress", mailbox="fi-to-witness")
            locator, content = self._locator_and_content(
                "fi-to-witness", packet, content_version="version-000002"
            )
            scanner = _FixedScanner(
                (locator,), {(locator.object_key, locator.object_version_id): content}
            )
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_CONTENT_INVALID",
            ):
                runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, scanner=scanner
                )

        with tempfile.TemporaryDirectory() as path:
            handle = self._open(Path(path), local_role="witness-fi-ingress", mailbox="fi-to-witness")
            locator_one, content_one = self._locator_and_content("fi-to-witness", packet)
            locator_two, content_two = self._locator_and_content(
                "fi-to-witness", packet, version="version-000002"
            )
            scanner = _FixedScanner(
                (locator_one, locator_two),
                {
                    (locator_one.object_key, locator_one.object_version_id): content_one,
                    (locator_two.object_key, locator_two.object_version_id): content_two,
                },
            )
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_LOCATOR_FORK",
            ):
                runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, scanner=scanner
                )
            self.assertEqual([], scanner.read_calls)

        with tempfile.TemporaryDirectory() as path:
            handle = self._open(Path(path), local_role="witness-fi-ingress", mailbox="fi-to-witness")
            tampered = packet[:-1] + (b"0" if packet[-1:] != b"0" else b"1")
            locator, content = self._locator_and_content("fi-to-witness", tampered)
            scanner = _FixedScanner(
                (locator,), {(locator.object_key, locator.object_version_id): content}
            )
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_DELIVERY_INVALID",
            ):
                runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, scanner=scanner
                )

    def test_inbound_replay_fork_clock_rollback_restart_and_post_reservation_failure(self) -> None:
        packet = self.deliveries["fi-to-witness"]
        locator, content = self._locator_and_content("fi-to-witness", packet)
        scanner = _FixedScanner(
            (locator,), {(locator.object_key, locator.object_version_id): content}
        )
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            handle = self._open(root, local_role="witness-fi-ingress", mailbox="fi-to-witness")
            with patch.object(runtime, "_host_now", return_value=NOW):
                first = runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, scanner=scanner
                )
                retry = runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, scanner=scanner
                )
            self.assertFalse(first[0].idempotent)
            self.assertTrue(retry[0].idempotent)
            alternate_locator, alternate_content = self._locator_and_content(
                "fi-to-witness", packet, version="version-000002"
            )
            alternate_scanner = _FixedScanner(
                (alternate_locator,),
                {(alternate_locator.object_key, alternate_locator.object_version_id): alternate_content},
            )
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_OBJECT_VERSION_FORK",
            ):
                runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, scanner=alternate_scanner
                )
            reopened = self._open(root, local_role="witness-fi-ingress", mailbox="fi-to-witness")
            with patch.object(runtime, "_host_now", return_value=NOW):
                persisted = runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
                    reopened, scanner=scanner
                )
            self.assertTrue(persisted[0].idempotent)
            with patch.object(runtime, "_host_now", return_value=NOW - timedelta(seconds=1)), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_CLOCK_ROLLBACK_DETECTED",
            ):
                runtime.open_physical_wal_v2_witness_roundtrip_delivery_runtime(
                    config=self._runtime_config(
                        root,
                        local_role="witness-fi-ingress",
                        mailbox="fi-to-witness",
                    )
                )

        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            handle = self._open(root, local_role="witness-fi-ingress", mailbox="fi-to-witness")
            original_write = runtime._write_state
            calls = 0

            def fail_second_write(*args: object, **kwargs: object):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError(
                        "TEST_POST_RESERVATION_WRITE_FAILURE"
                    )
                return original_write(*args, **kwargs)

            with patch.object(runtime, "_host_now", return_value=NOW), patch.object(
                runtime, "_write_state", side_effect=fail_second_write
            ), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "TEST_POST_RESERVATION_WRITE_FAILURE",
            ):
                runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, scanner=scanner
                )
            reopened = self._open(root, local_role="witness-fi-ingress", mailbox="fi-to-witness")
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_CONSUME_INDETERMINATE",
            ):
                runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
                    reopened, scanner=scanner
                )

    def test_raw_tampered_delivery_is_rejected_before_outbound_adapter(self) -> None:
        packet = self.deliveries["fi-to-witness"]
        with tempfile.TemporaryDirectory() as path:
            handle = self._open(
                Path(path), local_role="fi-writer-source-outbox", mailbox="fi-to-witness"
            )
            publisher = _FixedPublisher()
            tampered = packet[:-1] + (b"0" if packet[-1:] != b"0" else b"1")
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_DELIVERY_INVALID",
            ):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, tampered, publisher=publisher
                )
            self.assertEqual([], publisher.calls)

    def test_fresh_clock_fences_pre_post_callback_expiry_rollback_and_cached_retry(self) -> None:
        packet = self.deliveries["fi-to-witness"]
        expired = NOW + timedelta(seconds=26)

        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            handle = self._open(
                root, local_role="fi-writer-source-outbox", mailbox="fi-to-witness"
            )
            publisher = _FixedPublisher()
            with patch.object(runtime, "_host_now", side_effect=[NOW, expired]), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_DELIVERY_INVALID",
            ):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, packet, publisher=publisher
                )
            self.assertEqual([], publisher.calls)
            reopened = self._open(
                root, local_role="fi-writer-source-outbox", mailbox="fi-to-witness"
            )
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_PUBLISH_INDETERMINATE",
            ):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    reopened, packet, publisher=publisher
                )

        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            handle = self._open(
                root, local_role="fi-writer-source-outbox", mailbox="fi-to-witness"
            )
            publisher = _FixedPublisher()
            with patch.object(runtime, "_host_now", side_effect=[NOW, NOW, expired]), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_DELIVERY_INVALID",
            ):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, packet, publisher=publisher
                )
            self.assertEqual(1, len(publisher.calls))
            reopened = self._open(
                root, local_role="fi-writer-source-outbox", mailbox="fi-to-witness"
            )
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_PUBLISH_INDETERMINATE",
            ):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    reopened, packet, publisher=publisher
                )

        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            handle = self._open(
                root, local_role="fi-writer-source-outbox", mailbox="fi-to-witness"
            )
            publisher = _FixedPublisher()
            with patch.object(runtime, "_host_now", side_effect=[NOW, NOW, NOW - timedelta(seconds=1)]), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_CLOCK_ROLLBACK_DETECTED",
            ):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, packet, publisher=publisher
                )
            self.assertEqual(1, len(publisher.calls))
            reopened = self._open(
                root, local_role="fi-writer-source-outbox", mailbox="fi-to-witness"
            )
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_PUBLISH_INDETERMINATE",
            ):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    reopened, packet, publisher=publisher
                )

        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            handle = self._open(
                root, local_role="fi-writer-source-outbox", mailbox="fi-to-witness"
            )
            publisher = _FixedPublisher()
            with patch.object(runtime, "_host_now", side_effect=[NOW, NOW, NOW + timedelta(seconds=1)]):
                result = runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, packet, publisher=publisher
                )
            self.assertEqual(NOW + timedelta(seconds=1), result.committed_at)
            with patch.object(runtime, "_host_now", return_value=expired):
                reopened_after_expiry = (
                    runtime.open_physical_wal_v2_witness_roundtrip_delivery_runtime(
                        config=self._runtime_config(
                            root,
                            local_role="fi-writer-source-outbox",
                            mailbox="fi-to-witness",
                        )
                    )
                )
            with patch.object(runtime, "_host_now", return_value=expired), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_DELIVERY_INVALID",
            ):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    reopened_after_expiry, packet, publisher=publisher
                )
            self.assertEqual(1, len(publisher.calls))

        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            handle = self._open(
                root, local_role="fi-writer-source-outbox", mailbox="fi-to-witness"
            )
            publisher = _FixedPublisher()
            with patch.object(runtime, "_host_now", side_effect=[NOW, NOW, NOW]):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, packet, publisher=publisher
                )
            with patch.object(
                runtime,
                "_host_now",
                side_effect=[NOW + timedelta(seconds=2), NOW + timedelta(seconds=1)],
            ), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_CLOCK_ROLLBACK_DETECTED",
            ):
                runtime.publish_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, packet, publisher=publisher
                )
            self.assertEqual(1, len(publisher.calls))

        locator, content = self._locator_and_content("fi-to-witness", packet)
        scanner = _FixedScanner(
            (locator,), {(locator.object_key, locator.object_version_id): content}
        )
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            handle = self._open(root, local_role="witness-fi-ingress", mailbox="fi-to-witness")
            with patch.object(runtime, "_host_now", side_effect=[NOW, expired]), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_DELIVERY_INVALID",
            ):
                runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, scanner=scanner
                )
            self.assertEqual(1, len(scanner.read_calls))
            reopened = self._open(root, local_role="witness-fi-ingress", mailbox="fi-to-witness")
            with patch.object(runtime, "_host_now", return_value=NOW):
                result = runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
                    reopened, scanner=scanner
                )
            self.assertFalse(result[0].idempotent)

        locator, content = self._locator_and_content("fi-to-witness", packet)
        scanner = _FixedScanner(
            (locator,), {(locator.object_key, locator.object_version_id): content}
        )
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            handle = self._open(root, local_role="witness-fi-ingress", mailbox="fi-to-witness")
            with patch.object(runtime, "_host_now", side_effect=[NOW, NOW, expired]), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_DELIVERY_INVALID",
            ):
                runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
                    handle, scanner=scanner
                )
            reopened = self._open(root, local_role="witness-fi-ingress", mailbox="fi-to-witness")
            with patch.object(runtime, "_host_now", return_value=NOW), self.assertRaisesRegex(
                runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeError,
                "RUNTIME_CONSUME_INDETERMINATE",
            ):
                runtime.consume_physical_wal_v2_witness_fi_to_witness_delivery(
                    reopened, scanner=scanner
                )

    def test_static_surface_has_only_fixed_role_local_adapter_calls(self) -> None:
        source = inspect.getsource(runtime)
        tree = ast.parse(source)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        for forbidden in (
            "socket",
            "subprocess",
            "requests",
            "boto",
            "urllib",
            "httpx",
            "paramiko",
            "shutil",
        ):
            self.assertNotIn(forbidden, imported)
        self.assertNotIn("physical_wal_v1", source)
        self.assertNotIn("preflight", source)
        self.assertNotIn("os.unlink", source)
        self.assertNotIn("os.remove", source)
        public = set(runtime.__all__)
        self.assertNotIn("send", public)
        self.assertNotIn("publish_delivery", public)
        self.assertNotIn("consume_delivery", public)
        protocol_methods = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and (
                node.name.startswith("create_")
                or node.name.startswith("list_")
                or node.name.startswith("read_")
            )
        }
        self.assertEqual(
            {
                "create_fi_to_witness_delivery",
                "create_witness_to_ir_delivery",
                "create_ir_to_witness_delivery",
                "create_witness_to_fi_delivery",
                "list_fi_to_witness_delivery_locators",
                "read_fi_to_witness_delivery_exact",
                "list_witness_to_ir_delivery_locators",
                "read_witness_to_ir_delivery_exact",
                "list_ir_to_witness_delivery_locators",
                "read_ir_to_witness_delivery_exact",
                "list_witness_to_fi_delivery_locators",
                "read_witness_to_fi_delivery_exact",
            },
            protocol_methods,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
