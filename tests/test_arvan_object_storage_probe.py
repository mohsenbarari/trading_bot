import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from scripts import arvan_object_storage_probe as probe


class ArvanObjectStorageProbeTests(unittest.TestCase):
    def test_normalize_prefix_rejects_non_staging_prefix(self):
        with self.assertRaisesRegex(ValueError, "Refusing non-staging prefix"):
            probe.normalize_prefix("production/releases")

    def test_normalize_prefix_accepts_staging_prefix(self):
        self.assertEqual(probe.normalize_prefix("/staging/releases/"), "staging/releases")
        self.assertEqual(probe.normalize_prefix("staging-probe/foreign-only"), "staging-probe/foreign-only")

    def test_dry_run_masks_credentials_and_does_not_leak_secret(self):
        env = {
            "ARVAN_OBJECT_STORAGE_BUCKET": "staging-bucket",
            "ARVAN_OBJECT_STORAGE_ACCESS_KEY": "access-key-sensitive",
            "ARVAN_OBJECT_STORAGE_SECRET_KEY": "secret-key-sensitive",
        }
        output = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False):
            with redirect_stdout(output):
                exit_code = probe.main([])

        self.assertEqual(exit_code, 0)
        raw = output.getvalue()
        self.assertNotIn("access-key-sensitive", raw)
        self.assertNotIn("secret-key-sensitive", raw)

        payload = json.loads(raw)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dry_run"])
        self.assertTrue(payload["config"]["access_key_present"])
        self.assertTrue(payload["config"]["secret_key_present"])
        self.assertTrue(payload["config"]["access_key_hint"].endswith("tive"))

    def test_execute_requires_credentials(self):
        env = {
            "ARVAN_OBJECT_STORAGE_BUCKET": "staging-bucket",
            "ARVAN_OBJECT_STORAGE_ACCESS_KEY": "",
            "ARVAN_OBJECT_STORAGE_SECRET_KEY": "",
        }
        output = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False):
            with redirect_stdout(output):
                exit_code = probe.main(["--execute"])

        self.assertEqual(exit_code, 2)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertIn(probe.DEFAULT_ACCESS_KEY_ENV, payload["error"])
        self.assertIn(probe.DEFAULT_SECRET_KEY_ENV, payload["error"])

    def test_build_object_key_uses_staging_prefix(self):
        key = probe.build_object_key("staging/probe")
        self.assertTrue(key.startswith("staging/probe/"))
        self.assertTrue(key.endswith(".txt"))


if __name__ == "__main__":
    unittest.main()
