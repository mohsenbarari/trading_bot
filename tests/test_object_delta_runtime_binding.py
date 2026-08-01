from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from core.object_delta_runtime_binding import (
    OBJECT_DELTA_SOURCE_BINDING_SCHEMA,
    ObjectDeltaRuntimeBindingError,
    binding_from_settings,
    load_object_delta_source_binding,
    parse_object_delta_source_binding,
    validate_object_delta_source_runtime,
)


def binding_value(**overrides):
    value = {
        "schema": OBJECT_DELTA_SOURCE_BINDING_SCHEMA,
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "campaign_id": "wa-ir-standby-97265988-4b12-444e",
        "release_sha": "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
        "stream_generation_id": "fi-ir-delta-97265988-a",
        "expected_registry_fingerprint": "0123456789abcdef",
    }
    value.update(overrides)
    return value


class _Settings:
    object_delta_source_outbox_enabled = False
    single_writer_runtime_enabled = True
    application_writer_term_enforced = True
    object_delta_source_binding_file = None
    release_sha = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
    object_delta_receiver_delivery_enabled = False


class ObjectDeltaRuntimeBindingTests(unittest.TestCase):
    def write_binding(self, directory: Path, **overrides) -> Path:
        path = directory / "source-binding.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(json.dumps(binding_value(**overrides), sort_keys=True).encode("utf-8"))
        return path

    def test_parse_requires_the_exact_non_secret_schema(self):
        binding = parse_object_delta_source_binding(binding_value())

        self.assertEqual("foreign", binding.source_server)
        with self.assertRaisesRegex(ObjectDeltaRuntimeBindingError, "fields"):
            parse_object_delta_source_binding({"schema": OBJECT_DELTA_SOURCE_BINDING_SCHEMA})
        with self.assertRaisesRegex(ObjectDeltaRuntimeBindingError, "destination"):
            parse_object_delta_source_binding(binding_value(destination_site="webapp_fi"))

    def test_runtime_validation_binds_role_and_release_fingerprint(self):
        binding = parse_object_delta_source_binding(binding_value())

        self.assertIs(
            binding,
            validate_object_delta_source_runtime(
                binding,
                current_server="foreign",
                current_release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                current_registry_fingerprint="0123456789abcdef",
            ),
        )
        with self.assertRaisesRegex(ObjectDeltaRuntimeBindingError, "this server"):
            validate_object_delta_source_runtime(
                binding,
                current_server="iran",
                current_release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                current_registry_fingerprint="0123456789abcdef",
            )
        with self.assertRaisesRegex(ObjectDeltaRuntimeBindingError, "does not match this release"):
            validate_object_delta_source_runtime(
                binding,
                current_server="foreign",
                current_release_sha="2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                current_registry_fingerprint="fedcba9876543210",
            )
        with self.assertRaisesRegex(ObjectDeltaRuntimeBindingError, "release sha"):
            validate_object_delta_source_runtime(
                binding,
                current_server="foreign",
                current_release_sha="1" * 40,
                current_registry_fingerprint="0123456789abcdef",
            )

    def test_root_only_loader_rejects_insecure_leaf_and_duplicate_json(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            path = self.write_binding(directory)
            loaded = load_object_delta_source_binding(path)
            self.assertEqual("webapp_fi", loaded.source_site)

            path.chmod(0o644)
            with self.assertRaisesRegex(ObjectDeltaRuntimeBindingError, "root-only"):
                load_object_delta_source_binding(path)

            duplicate = directory / "duplicate.json"
            descriptor = os.open(duplicate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(
                    b'{"schema":"gold-trade-object-delta-source-binding-v1",'
                    b'"schema":"gold-trade-object-delta-source-binding-v1"}'
                )
            with self.assertRaisesRegex(ObjectDeltaRuntimeBindingError, "duplicate"):
                load_object_delta_source_binding(duplicate)

    def test_disabled_settings_do_not_touch_a_binding_file(self):
        settings = _Settings()
        settings.object_delta_source_binding_file = "/does/not/exist"

        self.assertIsNone(binding_from_settings(settings))

    def test_enabled_settings_require_both_existing_writer_fences_before_reading(self):
        settings = _Settings()
        settings.object_delta_source_outbox_enabled = True
        settings.single_writer_runtime_enabled = False
        settings.object_delta_source_binding_file = "/does/not/exist"

        with self.assertRaisesRegex(ObjectDeltaRuntimeBindingError, "single-writer"):
            binding_from_settings(settings)

        settings.single_writer_runtime_enabled = True
        settings.application_writer_term_enforced = False
        with self.assertRaisesRegex(ObjectDeltaRuntimeBindingError, "application writer terms"):
            binding_from_settings(settings)

        settings.application_writer_term_enforced = True
        settings.object_delta_receiver_delivery_enabled = True
        with self.assertRaisesRegex(ObjectDeltaRuntimeBindingError, "receiver delivery"):
            binding_from_settings(settings)


if __name__ == "__main__":
    unittest.main()
