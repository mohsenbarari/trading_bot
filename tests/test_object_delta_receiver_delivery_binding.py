from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.object_delta_delivery_control_packet import controller_key_id_from_public_key
from core.object_delta_source_batch_attestation import source_key_id_from_public_key
from core.object_delta_receiver_delivery_binding import (
    OBJECT_DELTA_RECEIVER_DELIVERY_BINDING_SCHEMA,
    ObjectDeltaReceiverDeliveryBindingError,
    load_object_delta_receiver_delivery_binding,
    parse_object_delta_receiver_delivery_binding,
    receiver_delivery_binding_from_settings,
    validate_object_delta_receiver_delivery_runtime,
)


FI_RECIPIENT = "age1" + "a" * 30
IR_RECIPIENT = "age1" + "c" * 30
CONTROLLER_PUBLIC_KEY = b"k" * 32
CONTROLLER_PUBLIC_KEY_BASE64 = base64.b64encode(CONTROLLER_PUBLIC_KEY).decode("ascii")
CONTROLLER_KEY_ID = controller_key_id_from_public_key(CONTROLLER_PUBLIC_KEY)
SOURCE_PUBLIC_KEY = b"s" * 32
SOURCE_PUBLIC_KEY_BASE64 = base64.b64encode(SOURCE_PUBLIC_KEY).decode("ascii")
SOURCE_KEY_ID = source_key_id_from_public_key(SOURCE_PUBLIC_KEY)


def binding_value(**overrides):
    value = {
        "schema": OBJECT_DELTA_RECEIVER_DELIVERY_BINDING_SCHEMA,
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "campaign_id": "wa-ir-receiver-delivery-97265988",
        "release_sha": "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
        "stream_generation_id": "fi-ir-receiver-delivery-97265988",
        "expected_registry_fingerprint": "0123456789abcdef",
        "bucket": "private-delta-bucket",
        "prefix": "campaigns/three-site",
        "webapp_fi_age_recipient": FI_RECIPIENT,
        "webapp_ir_age_recipient": IR_RECIPIENT,
        "destination_age_recipient": IR_RECIPIENT,
        "source_public_key_base64": SOURCE_PUBLIC_KEY_BASE64,
        "source_key_id": SOURCE_KEY_ID,
        "controller_public_key_base64": CONTROLLER_PUBLIC_KEY_BASE64,
        "controller_key_id": CONTROLLER_KEY_ID,
        "writer_epoch": 7,
        "writer_lease_id": "writer-lease-7",
    }
    value.update(overrides)
    return value


class _Settings:
    object_delta_receiver_delivery_enabled = False
    object_delta_receiver_delivery_permit_file = None
    object_delta_receiver_delivery_local_site = "webapp_ir"
    single_writer_runtime_enabled = True
    application_writer_term_enforced = True
    release_sha = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
    object_delta_source_outbox_enabled = False


class ObjectDeltaReceiverDeliveryBindingTests(unittest.TestCase):
    def write_binding(self, directory: Path, **overrides) -> Path:
        path = directory / "receiver-delivery-permit.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(json.dumps(binding_value(**overrides), sort_keys=True).encode("utf-8"))
        return path

    def test_parse_requires_exact_non_secret_schema_and_policy_bound_permit(self):
        binding = parse_object_delta_receiver_delivery_binding(binding_value())

        self.assertEqual("webapp_ir", binding.permit.destination_site)
        self.assertEqual("writer-lease-7", binding.permit.writer_lease_id)
        self.assertEqual("controller", binding.policy.credential_holder)
        self.assertEqual(CONTROLLER_PUBLIC_KEY, binding.controller_public_key)
        self.assertEqual(SOURCE_PUBLIC_KEY, binding.source_public_key)
        self.assertEqual(SOURCE_KEY_ID, binding.source_key_id)
        self.assertEqual("0123456789abcdef", binding.expected_registry_fingerprint)
        for forbidden_field in ("presigned_url", "s3_secret_access_key", "controller_private_key", "payload"):
            with self.subTest(forbidden_field=forbidden_field):
                with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "fields"):
                    parse_object_delta_receiver_delivery_binding(
                        binding_value(**{forbidden_field: "forbidden"})
                    )
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "invalid"):
            parse_object_delta_receiver_delivery_binding(
                binding_value(bucket="https://storage.example.invalid")
            )
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "key ID does not match"):
            parse_object_delta_receiver_delivery_binding(
                binding_value(controller_key_id="ed25519-sha256:" + "d" * 64)
            )
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "public key is invalid"):
            parse_object_delta_receiver_delivery_binding(
                binding_value(controller_public_key_base64="not-base64")
            )
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "source key ID does not match"):
            parse_object_delta_receiver_delivery_binding(
                binding_value(source_key_id="ed25519-sha256:" + "d" * 64)
            )
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "registry fingerprint"):
            parse_object_delta_receiver_delivery_binding(
                binding_value(expected_registry_fingerprint="not-a-fingerprint")
            )

    def test_runtime_validation_binds_local_receiver_site_and_release(self):
        binding = parse_object_delta_receiver_delivery_binding(binding_value())

        self.assertIs(
            binding,
            validate_object_delta_receiver_delivery_runtime(
                binding,
                current_site="webapp_ir",
                current_release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                current_registry_fingerprint="0123456789abcdef",
            ),
        )
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "does not match this site"):
            validate_object_delta_receiver_delivery_runtime(
                binding,
                current_site="webapp_fi",
                current_release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                current_registry_fingerprint="0123456789abcdef",
            )
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "does not match this release"):
            validate_object_delta_receiver_delivery_runtime(
                binding,
                current_site="webapp_ir",
                current_release_sha="1" * 40,
                current_registry_fingerprint="0123456789abcdef",
            )
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "registry fingerprint"):
            validate_object_delta_receiver_delivery_runtime(
                binding,
                current_site="webapp_ir",
                current_release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                current_registry_fingerprint="f" * 16,
            )

    def test_root_only_loader_rejects_mode_duplicate_and_symlink_paths(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            path = self.write_binding(directory)
            loaded = load_object_delta_receiver_delivery_binding(path)
            self.assertEqual("private-delta-bucket", loaded.policy.bucket)

            path.chmod(0o644)
            with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "root-only 0600"):
                load_object_delta_receiver_delivery_binding(path)
            path.chmod(0o600)

            duplicate = directory / "duplicate.json"
            descriptor = os.open(duplicate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(
                    b'{"schema":"gold-trade-object-delta-receiver-delivery-binding-v1",'
                    b'"schema":"gold-trade-object-delta-receiver-delivery-binding-v1"}'
                )
            with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "duplicate"):
                load_object_delta_receiver_delivery_binding(duplicate)

            leaf_link = directory / "permit-link.json"
            os.symlink(path, leaf_link)
            with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "cannot be opened safely"):
                load_object_delta_receiver_delivery_binding(leaf_link)

            parent_link = directory / "permit-parent-link"
            os.symlink(directory, parent_link)
            with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "parent is unsafe"):
                load_object_delta_receiver_delivery_binding(parent_link / path.name)

    def test_disabled_settings_do_not_touch_a_permit_file(self):
        settings = _Settings()
        settings.object_delta_receiver_delivery_permit_file = "/does/not/exist"

        self.assertIsNone(receiver_delivery_binding_from_settings(settings))

    def test_enabled_settings_require_fences_before_reading_and_project_valid_permit(self):
        settings = _Settings()
        settings.object_delta_receiver_delivery_enabled = True
        settings.single_writer_runtime_enabled = False
        settings.object_delta_receiver_delivery_permit_file = "/does/not/exist"
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "single-writer"):
            receiver_delivery_binding_from_settings(settings)

        settings.single_writer_runtime_enabled = True
        settings.application_writer_term_enforced = False
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "application writer terms"):
            receiver_delivery_binding_from_settings(settings)

        settings.application_writer_term_enforced = True
        settings.object_delta_source_outbox_enabled = True
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "source outbox"):
            receiver_delivery_binding_from_settings(settings)
        settings.object_delta_source_outbox_enabled = False
        settings.object_delta_receiver_delivery_local_site = None
        with self.assertRaisesRegex(ObjectDeltaReceiverDeliveryBindingError, "local site"):
            receiver_delivery_binding_from_settings(settings)
        settings.object_delta_receiver_delivery_local_site = "webapp_ir"

        with tempfile.TemporaryDirectory() as raw_directory:
            path = self.write_binding(
                Path(raw_directory),
                expected_registry_fingerprint="0123456789abcdef",
            )
            settings.object_delta_receiver_delivery_permit_file = path
            with patch(
                "core.sync_protocol.current_sync_registry_fingerprint",
                return_value="0123456789abcdef",
            ):
                projected = receiver_delivery_binding_from_settings(settings)
            self.assertEqual("webapp_ir", projected.permit.destination_site)
            with patch(
                "core.sync_protocol.current_sync_registry_fingerprint",
                return_value="f" * 16,
            ):
                with self.assertRaisesRegex(
                    ObjectDeltaReceiverDeliveryBindingError,
                    "release registry fingerprint",
                ):
                    receiver_delivery_binding_from_settings(settings)

if __name__ == "__main__":
    unittest.main()
