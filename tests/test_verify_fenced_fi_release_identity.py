from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import tempfile
from unittest import TestCase, mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.fenced_fi_release_identity import canonical_fenced_fi_release_identity_json_bytes
from scripts import verify_fenced_fi_release_identity as subject


def _write_private(path: Path, value: bytes) -> None:
    path.write_bytes(value)
    path.chmod(0o600)


class VerifyFencedFiReleaseIdentityTests(TestCase):
    def _fixture(self, directory: Path) -> tuple[Path, Path]:
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        authority = directory / "authority.pub"
        _write_private(authority, base64.b64encode(public) + b"\n")
        unsigned: dict[str, object] = {
            "schema": "gold-trade-wa-fi-fenced-release-identity-v3",
            "release_sha": "a" * 40,
            "release_tree_sha": "b" * 40,
            "application_release_root": "/srv/releases/" + "a" * 40,
            "control_release_sha": "c" * 40,
            "control_release_tree_sha": "d" * 40,
            "control_release_root": "/srv/control/" + "c" * 40,
            "compose_relative_path": "deploy/production/docker-compose.webapp-fi-writer-release-v1.yml",
            "compose_sha256": "e" * 64,
            "term_fenced_application_evidence_sha256": "9" * 64,
            "fenced_fi_build_input": {
                "build_input_manifest_sha256": "4" * 64,
                "mini_app_dist_manifest_sha256": "5" * 64,
                "mini_app_dist_files_sha256": "6" * 64,
                "mini_app_dist_file_count": 17,
                "mini_app_dist_total_bytes": 4096,
            },
            "services": {
                "app": {"image_repo_digest": "registry.invalid/app@sha256:" + "f" * 64, "image_id": "sha256:" + "1" * 64},
                "bot": {"image_repo_digest": "registry.invalid/bot@sha256:" + "2" * 64, "image_id": "sha256:" + "3" * 64},
            },
            "signer_key_id": "ed25519-sha256:" + hashlib.sha256(public).hexdigest(),
        }
        signature = private.sign(
            b"gold-trade-wa-fi-fenced-release-identity-v3\x00"
            + canonical_fenced_fi_release_identity_json_bytes(unsigned)
        )
        document = dict(unsigned)
        document["signature_base64"] = base64.b64encode(signature).decode("ascii")
        descriptor = directory / "identity.json"
        _write_private(descriptor, canonical_fenced_fi_release_identity_json_bytes(document))
        return descriptor, authority

    def test_valid_descriptor_is_explicitly_non_authorizing(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.object(os, "geteuid", return_value=0):
            descriptor, authority = self._fixture(Path(raw))
            result = subject.verify(descriptor_path=descriptor, authority_path=authority)
        self.assertEqual("verified-non-authorizing", result["status"])
        self.assertFalse(result["writer_authorized"])
        self.assertFalse(result["promotion_authorized"])
        self.assertFalse(result["execution_authorized"])

    def test_expected_identity_hash_prevents_valid_descriptor_replay(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.object(os, "geteuid", return_value=0):
            descriptor, authority = self._fixture(Path(raw))
            expected = hashlib.sha256(descriptor.read_bytes()).hexdigest()
            result = subject.verify(
                descriptor_path=descriptor,
                authority_path=authority,
                expected_identity_sha256=expected,
            )
            self.assertEqual(expected, result["identity_sha256"])
            with self.assertRaisesRegex(
                subject.VerifyFencedFiReleaseIdentityError, "expected descriptor hash"
            ):
                subject.verify(
                    descriptor_path=descriptor,
                    authority_path=authority,
                    expected_identity_sha256="0" * 64,
                )

    def test_tampered_descriptor_and_non_owner_mode_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.object(os, "geteuid", return_value=0):
            descriptor, authority = self._fixture(Path(raw))
            descriptor.write_bytes(
                descriptor.read_bytes().replace(
                    b'"compose_sha256":"' + b"e" * 64,
                    b'"compose_sha256":"' + b"a" * 64,
                )
            )
            descriptor.chmod(0o600)
            with self.assertRaisesRegex(subject.VerifyFencedFiReleaseIdentityError, "SIGNATURE_INVALID"):
                subject.verify(descriptor_path=descriptor, authority_path=authority)
            descriptor, authority = self._fixture(Path(raw))
            authority.chmod(0o644)
            with self.assertRaisesRegex(subject.VerifyFencedFiReleaseIdentityError, "owner-only"):
                subject.verify(descriptor_path=descriptor, authority_path=authority)

    def test_platform_without_no_follow_is_rejected_before_open(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.object(os, "geteuid", return_value=0):
            descriptor, authority = self._fixture(Path(raw))
            with mock.patch.object(subject.os, "O_NOFOLLOW", create=True, new=None):
                with self.assertRaisesRegex(subject.VerifyFencedFiReleaseIdentityError, "O_NOFOLLOW"):
                    subject.verify(descriptor_path=descriptor, authority_path=authority)
