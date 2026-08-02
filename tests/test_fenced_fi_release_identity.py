"""Adversarial tests for the inert, signed WA-FI Release-0 identity."""

from __future__ import annotations

import base64
import copy
import hashlib
from unittest import TestCase

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import fenced_fi_release_identity as subject


class FencedFiReleaseIdentityTests(TestCase):
    def setUp(self) -> None:
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self.authority = subject.FencedFiReleaseIdentityAuthority(
            public_key=public,
            key_id="ed25519-sha256:" + hashlib.sha256(public).hexdigest(),
        )

    def _signed_document(self, value: dict[str, object]) -> bytes:
        unsigned = dict(value)
        unsigned.pop("signature_base64", None)
        schema = unsigned.get("schema")
        domains = {
            subject.FENCED_FI_RELEASE_IDENTITY_SCHEMA: (
                b"gold-trade-wa-fi-fenced-release-identity-v3\x00"
            ),
            subject.FENCED_FI_RELEASE_IDENTITY_TERM_FENCED_LEGACY_SCHEMA: (
                b"gold-trade-wa-fi-fenced-release-identity-v2\x00"
            ),
            subject.FENCED_FI_RELEASE_IDENTITY_LEGACY_SCHEMA: (
                b"gold-trade-wa-fi-fenced-release-identity-v1\x00"
            ),
        }
        domain = domains[schema]
        signature = self.private.sign(
            domain
            + subject.canonical_fenced_fi_release_identity_json_bytes(unsigned)
        )
        signed = dict(unsigned)
        signed["signature_base64"] = base64.b64encode(signature).decode("ascii")
        return subject.canonical_fenced_fi_release_identity_json_bytes(signed)

    def _document(self, **changes: object) -> bytes:
        value: dict[str, object] = {
            "schema": subject.FENCED_FI_RELEASE_IDENTITY_SCHEMA,
            "release_sha": "a" * 40,
            "release_tree_sha": "b" * 40,
            "application_release_root": "/srv/trading-bot-three-site/releases/" + "a" * 40,
            "control_release_sha": "c" * 40,
            "control_release_tree_sha": "d" * 40,
            "control_release_root": "/srv/trading-bot-three-site/control/" + "c" * 40,
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
            "signer_key_id": self.authority.key_id,
        }
        value.update(changes)
        return self._signed_document(value)

    def test_valid_identity_is_non_authorizing(self) -> None:
        value = subject.verify_fenced_fi_release_identity(self._document(), authority=self.authority)
        self.assertEqual("a" * 40, value.release_sha)
        self.assertEqual("registry.invalid/app@sha256:" + "f" * 64, value.app_image_repo_digest)
        self.assertEqual(subject.FENCED_FI_RELEASE_IDENTITY_SCHEMA, value.schema)
        self.assertEqual("9" * 64, value.term_fenced_application_evidence_sha256)
        self.assertEqual("4" * 64, value.static_build_input.build_input_manifest_sha256)
        self.assertEqual("5" * 64, value.static_build_input.mini_app_dist_manifest_sha256)
        self.assertEqual("6" * 64, value.static_build_input.mini_app_dist_files_sha256)
        self.assertEqual(17, value.static_build_input.mini_app_dist_file_count)
        self.assertEqual(4096, value.static_build_input.mini_app_dist_total_bytes)
        self.assertFalse(value.writer_authorized)
        self.assertFalse(value.promotion_authorized)
        self.assertFalse(value.execution_authorized)
        self.assertFalse(value.full_matrix_authorized)
        self.assertFalse(value.full_matrix_executed)
        self.assertIs(value, subject.require_verified_fenced_fi_release_identity(value))
        self.assertIs(value, subject.require_term_fenced_fi_release_candidate(value))

    def test_verified_identity_copy_and_mutation_are_rejected(self) -> None:
        value = subject.verify_fenced_fi_release_identity(self._document(), authority=self.authority)
        with self.assertRaisesRegex(TypeError, "COPY_FORBIDDEN"):
            copy.copy(value)
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            value.__reduce_ex__(4)
        object.__setattr__(value, "release_sha", "0" * 40)
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "UNVERIFIED"):
            subject.require_verified_fenced_fi_release_identity(value)

    def test_tamper_wrong_authority_and_tag_only_image_are_rejected(self) -> None:
        raw = self._document()
        changed = raw.replace(b'"compose_sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"', b'"compose_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"')
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "SIGNATURE_INVALID"):
            subject.verify_fenced_fi_release_identity(changed, authority=self.authority)
        other = subject.FencedFiReleaseIdentityAuthority(public_key=Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw), key_id=self.authority.key_id)
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "AUTHORITY_INVALID|SIGNATURE_INVALID"):
            subject.verify_fenced_fi_release_identity(raw, authority=other)
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "SERVICE_INVALID"):
            subject.verify_fenced_fi_release_identity(self._document(services={"app": {"image_repo_digest": "registry.invalid/app:latest", "image_id": "sha256:" + "1" * 64}, "bot": {"image_repo_digest": "registry.invalid/bot@sha256:" + "2" * 64, "image_id": "sha256:" + "3" * 64}}), authority=self.authority)

    def test_noncanonical_duplicate_and_root_mismatch_are_rejected(self) -> None:
        raw = self._document()
        noncanonical = raw.replace(b",\"signature_base64\"", b", \"signature_base64\"")
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "CANONICAL_REQUIRED"):
            subject.verify_fenced_fi_release_identity(noncanonical, authority=self.authority)
        duplicate = b'{"schema":"x","schema":"x"}'
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "DUPLICATE_FIELD"):
            subject.verify_fenced_fi_release_identity(duplicate, authority=self.authority)
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "APPLICATION_ROOT_INVALID"):
            subject.verify_fenced_fi_release_identity(self._document(application_release_root="/srv/releases/" + "b" * 40), authority=self.authority)

    def test_signed_key_id_mismatch_and_signature_mismatch_are_rejected(self) -> None:
        wrong_key_id = "ed25519-sha256:" + "0" * 64
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "SIGNER_MISMATCH"):
            subject.verify_fenced_fi_release_identity(
                self._document(signer_key_id=wrong_key_id), authority=self.authority
            )
        unsigned = {
            "schema": subject.FENCED_FI_RELEASE_IDENTITY_SCHEMA,
            "release_sha": "a" * 40,
            "release_tree_sha": "b" * 40,
            "application_release_root": "/srv/trading-bot-three-site/releases/" + "a" * 40,
            "control_release_sha": "c" * 40,
            "control_release_tree_sha": "d" * 40,
            "control_release_root": "/srv/trading-bot-three-site/control/" + "c" * 40,
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
            "signer_key_id": self.authority.key_id,
        }
        other_private = Ed25519PrivateKey.generate()
        forged = dict(unsigned)
        forged["signature_base64"] = base64.b64encode(
            other_private.sign(
                b"gold-trade-wa-fi-fenced-release-identity-v3\x00"
                + subject.canonical_fenced_fi_release_identity_json_bytes(unsigned)
            )
        ).decode("ascii")
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "SIGNATURE_INVALID"):
            subject.verify_fenced_fi_release_identity(
                subject.canonical_fenced_fi_release_identity_json_bytes(forged), authority=self.authority
            )

    def test_missing_or_unknown_top_level_field_is_rejected_before_signature_use(self) -> None:
        missing = {
            "schema": subject.FENCED_FI_RELEASE_IDENTITY_SCHEMA,
            "release_sha": "a" * 40,
            "release_tree_sha": "b" * 40,
            "application_release_root": "/srv/trading-bot-three-site/releases/" + "a" * 40,
            "control_release_sha": "c" * 40,
            "control_release_tree_sha": "d" * 40,
            "control_release_root": "/srv/trading-bot-three-site/control/" + "c" * 40,
            "compose_relative_path": "deploy/production/docker-compose.webapp-fi-writer-release-v1.yml",
            "term_fenced_application_evidence_sha256": "9" * 64,
            "services": {
                "app": {"image_repo_digest": "registry.invalid/app@sha256:" + "f" * 64, "image_id": "sha256:" + "1" * 64},
                "bot": {"image_repo_digest": "registry.invalid/bot@sha256:" + "2" * 64, "image_id": "sha256:" + "3" * 64},
            },
            "signer_key_id": self.authority.key_id,
        }
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "FIELDS_INVALID"):
            subject.verify_fenced_fi_release_identity(
                self._signed_document(missing), authority=self.authority
            )
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "FIELDS_INVALID"):
            subject.verify_fenced_fi_release_identity(
                self._document(unexpected_field="must-not-be-ignored"), authority=self.authority
            )

    def test_wrong_compose_path_and_control_root_are_rejected_despite_valid_signature(self) -> None:
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "COMPOSE_PATH_INVALID"):
            subject.verify_fenced_fi_release_identity(
                self._document(
                    compose_relative_path="deploy/production/docker-compose.webapp-fi-writer-release-v1.yaml"
                ),
                authority=self.authority,
            )
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "CONTROL_ROOT_INVALID"):
            subject.verify_fenced_fi_release_identity(
                self._document(
                    control_release_root="/srv/trading-bot-three-site/control/" + "e" * 40
                ),
                authority=self.authority,
            )

    def test_deep_json_is_a_typed_refusal_not_a_recursion_traceback(self) -> None:
        document = b'{"schema":' + (b"[" * 1200) + b"0" + (b"]" * 1200) + b"}"
        with self.assertRaisesRegex(
            subject.FencedFiReleaseIdentityError, "DOCUMENT_INVALID|FIELDS_INVALID|SCHEMA_INVALID"
        ):
            subject.verify_fenced_fi_release_identity(document, authority=self.authority)

    def test_legacy_v1_remains_read_only_parseable_but_cannot_be_a_candidate(self) -> None:
        legacy = {
            "schema": subject.FENCED_FI_RELEASE_IDENTITY_LEGACY_SCHEMA,
            "release_sha": "a" * 40,
            "release_tree_sha": "b" * 40,
            "application_release_root": "/srv/releases/" + "a" * 40,
            "control_release_sha": "c" * 40,
            "control_release_tree_sha": "d" * 40,
            "control_release_root": "/srv/control/" + "c" * 40,
            "compose_relative_path": "deploy/production/docker-compose.webapp-fi-writer-release-v1.yml",
            "compose_sha256": "e" * 64,
            "services": {
                "app": {"image_repo_digest": "registry.invalid/app@sha256:" + "f" * 64, "image_id": "sha256:" + "1" * 64},
                "bot": {"image_repo_digest": "registry.invalid/bot@sha256:" + "2" * 64, "image_id": "sha256:" + "3" * 64},
            },
            "signer_key_id": self.authority.key_id,
        }
        parsed = subject.verify_fenced_fi_release_identity(
            self._signed_document(legacy), authority=self.authority
        )
        self.assertEqual(subject.FENCED_FI_RELEASE_IDENTITY_LEGACY_SCHEMA, parsed.schema)
        self.assertIsNone(parsed.term_fenced_application_evidence_sha256)
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "TERM_FENCED_CANDIDATE_REQUIRED"):
            subject.require_term_fenced_fi_release_candidate(parsed)

    def test_legacy_v2_remains_read_only_parseable_but_cannot_be_a_candidate(self) -> None:
        legacy = {
            "schema": subject.FENCED_FI_RELEASE_IDENTITY_TERM_FENCED_LEGACY_SCHEMA,
            "release_sha": "a" * 40,
            "release_tree_sha": "b" * 40,
            "application_release_root": "/srv/releases/" + "a" * 40,
            "control_release_sha": "c" * 40,
            "control_release_tree_sha": "d" * 40,
            "control_release_root": "/srv/control/" + "c" * 40,
            "compose_relative_path": "deploy/production/docker-compose.webapp-fi-writer-release-v1.yml",
            "compose_sha256": "e" * 64,
            "term_fenced_application_evidence_sha256": "9" * 64,
            "services": {
                "app": {"image_repo_digest": "registry.invalid/app@sha256:" + "f" * 64, "image_id": "sha256:" + "1" * 64},
                "bot": {"image_repo_digest": "registry.invalid/bot@sha256:" + "2" * 64, "image_id": "sha256:" + "3" * 64},
            },
            "signer_key_id": self.authority.key_id,
        }
        parsed = subject.verify_fenced_fi_release_identity(
            self._signed_document(legacy), authority=self.authority
        )
        self.assertEqual(subject.FENCED_FI_RELEASE_IDENTITY_TERM_FENCED_LEGACY_SCHEMA, parsed.schema)
        self.assertEqual("9" * 64, parsed.term_fenced_application_evidence_sha256)
        self.assertIsNone(parsed.static_build_input)
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "TERM_FENCED_CANDIDATE_REQUIRED"):
            subject.require_term_fenced_fi_release_candidate(parsed)

    def test_static_build_input_and_required_image_labels_are_closed(self) -> None:
        identity = subject.verify_fenced_fi_release_identity(
            self._document(), authority=self.authority
        )
        labels = subject.expected_fenced_fi_static_image_labels(identity.static_build_input)
        subject.verify_fenced_fi_static_image_labels(
            labels,
            value=identity.static_build_input,
        )
        labels["org.goldtrade.mini-app-dist-total-bytes"] = "4097"
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "STATIC_IMAGE_LABELS_INVALID"):
            subject.verify_fenced_fi_static_image_labels(
                labels,
                value=identity.static_build_input,
            )
        with self.assertRaisesRegex(subject.FencedFiReleaseIdentityError, "STATIC_BUILD_INPUT_INVALID"):
            subject.verify_fenced_fi_release_identity(
                self._document(
                    fenced_fi_build_input={
                        "build_input_manifest_sha256": "4" * 64,
                        "mini_app_dist_manifest_sha256": "5" * 64,
                        "mini_app_dist_files_sha256": "6" * 64,
                        "mini_app_dist_file_count": True,
                        "mini_app_dist_total_bytes": 4096,
                    }
                ),
                authority=self.authority,
            )
