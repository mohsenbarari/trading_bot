from __future__ import annotations

from pathlib import Path
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts import production_shadow_ed25519_verifier as module


class ProductionShadowEd25519VerifierTests(unittest.TestCase):
    def test_valid_ephemeral_signature_is_accepted(self):
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        payload = b'{"canonical":"payload"}'
        module.verify_ed25519(public_key, private_key.sign(payload), payload)

    def test_non_exact_or_tampered_values_fail_closed(self):
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        payload = b"receipt"
        signature = private_key.sign(payload)
        invalid_values = (
            (bytearray(public_key), signature, payload),
            (public_key[:-1], signature, payload),
            (public_key, signature[:-1], payload),
            (public_key, signature, b""),
            (public_key, signature, payload + b"-altered"),
        )
        for candidate in invalid_values:
            with self.subTest(candidate=candidate):
                with self.assertRaises(module.Ed25519VerificationError):
                    module.verify_ed25519(*candidate)

    def test_missing_dependency_fails_closed(self):
        with mock.patch.object(module, "_Ed25519PublicKey", None):
            with self.assertRaises(module.Ed25519VerificationError):
                module.verify_ed25519(b"p" * 32, b"s" * 64, b"payload")

    def test_adapter_exposes_no_private_key_or_transport_behavior(self):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "Ed25519PrivateKey",
            ".generate(",
            ".sign(",
            "import subprocess",
            "import socket",
            "import urllib",
            "open(",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
