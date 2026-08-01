"""Adversarial tests for the inert signed Release-0 candidate contract."""

from __future__ import annotations

import base64
import copy
import hashlib
from unittest import TestCase

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import release0_immutable_candidate as subject


class Release0ImmutableCandidateTests(TestCase):
    def setUp(self) -> None:
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        self.authority = subject.Release0CandidateAuthority(
            public_key=public,
            key_id="ed25519-sha256:" + hashlib.sha256(public).hexdigest(),
        )

    def _signed_document(self, value: dict[str, object]) -> bytes:
        unsigned = dict(value)
        unsigned.pop("signature_base64", None)
        signature = self.private.sign(
            b"gold-trade-release0-immutable-candidate-v1\x00"
            + subject.canonical_release0_immutable_candidate_json_bytes(unsigned)
        )
        signed = dict(unsigned)
        signed["signature_base64"] = base64.b64encode(signature).decode("ascii")
        return subject.canonical_release0_immutable_candidate_json_bytes(signed)

    def _document(self, **changes: object) -> bytes:
        application_sha = "a" * 40
        control_sha = "b" * 40
        value: dict[str, object] = {
            "schema": subject.RELEASE0_IMMUTABLE_CANDIDATE_SCHEMA,
            "candidate_id": "release0-" + application_sha[:12] + "-" + control_sha[:12],
            "application": {
                "release_sha": application_sha,
                "tree_sha": "c" * 40,
                "release_root": "/srv/trading-bot-three-site/releases/" + application_sha,
            },
            "control": {
                "release_sha": control_sha,
                "tree_sha": "d" * 40,
                "release_root": "/srv/trading-bot-three-site/control-releases/" + control_sha,
            },
            "images": {
                "app": {
                    "image_repo_digest": "registry.invalid/release0-app@sha256:" + "e" * 64,
                    "image_id": "sha256:" + "1" * 64,
                },
                "bot": {
                    "image_repo_digest": "registry.invalid/release0-bot@sha256:" + "f" * 64,
                    "image_id": "sha256:" + "2" * 64,
                },
            },
            "term_contract": {
                "schema": subject.RELEASE0_TERM_CONTRACT_SCHEMA,
                "single_writer_runtime_enabled": True,
                "application_writer_term_enforced": True,
                "database_schema_bootstrap_enabled": False,
                "api_background_jobs_enabled": False,
                "lease_duration_seconds": 60,
                "safety_margin_seconds": 15,
                "renew_interval_seconds": 10,
            },
            "critical_source_files": {
                path: hashlib.sha256(path.encode("ascii")).hexdigest()
                for path in subject.CANDIDATE_CRITICAL_SOURCE_FILES
            },
            "compose": {
                "fi_writer_relative_path": "deploy/production/docker-compose.webapp-fi-writer-release0.yml",
                "fi_writer_sha256": "3" * 64,
                "ir_promoted_relative_path": "deploy/production/docker-compose.webapp-ir-promoted-release0.yml",
                "ir_promoted_sha256": "4" * 64,
            },
            "signer_key_id": self.authority.key_id,
        }
        value.update(changes)
        return self._signed_document(value)

    def test_valid_candidate_is_explicitly_non_authorizing(self) -> None:
        candidate = subject.verify_release0_immutable_candidate(
            self._document(), authority=self.authority
        )

        self.assertEqual("a" * 40, candidate.application.release_sha)
        self.assertEqual("b" * 40, candidate.control.release_sha)
        self.assertEqual(60, candidate.term_contract.lease_duration_seconds)
        self.assertEqual(subject.CANDIDATE_CRITICAL_SOURCE_FILES, tuple(path for path, _ in candidate.critical_source_files))
        self.assertFalse(candidate.writer_authorized)
        self.assertFalse(candidate.promotion_authorized)
        self.assertFalse(candidate.execution_authorized)
        self.assertFalse(candidate.full_matrix_authorized)
        self.assertFalse(candidate.full_matrix_executed)
        self.assertIs(candidate, subject.require_verified_release0_immutable_candidate(candidate))

    def test_legacy_2c08_is_hard_rejected_even_when_signed(self) -> None:
        legacy = subject.LEGACY_APPLICATION_RELEASE_SHA
        control_sha = "b" * 40
        with self.assertRaisesRegex(subject.Release0ImmutableCandidateError, "LEGACY_2C08_REJECTED"):
            subject.verify_release0_immutable_candidate(
                self._document(
                    candidate_id="release0-" + legacy[:12] + "-" + control_sha[:12],
                    application={
                        "release_sha": legacy,
                        "tree_sha": "c" * 40,
                        "release_root": "/srv/releases/" + legacy,
                    },
                ),
                authority=self.authority,
            )
        with self.assertRaisesRegex(subject.Release0ImmutableCandidateError, "LEGACY_2C08_REJECTED"):
            subject.verify_release0_immutable_candidate(
                self._document(
                    control={
                        "release_sha": legacy,
                        "tree_sha": "d" * 40,
                        "release_root": "/srv/control/" + legacy,
                    }
                ),
                authority=self.authority,
            )

    def test_term_contract_and_future_compose_paths_are_closed(self) -> None:
        term = {
            "schema": subject.RELEASE0_TERM_CONTRACT_SCHEMA,
            "single_writer_runtime_enabled": True,
            "application_writer_term_enforced": True,
            "database_schema_bootstrap_enabled": False,
            "api_background_jobs_enabled": True,
            "lease_duration_seconds": 60,
            "safety_margin_seconds": 15,
            "renew_interval_seconds": 10,
        }
        with self.assertRaisesRegex(subject.Release0ImmutableCandidateError, "TERM_CONTRACT_INVALID"):
            subject.verify_release0_immutable_candidate(
                self._document(term_contract=term), authority=self.authority
            )
        with self.assertRaisesRegex(subject.Release0ImmutableCandidateError, "TERM_CONTRACT_INVALID"):
            subject.verify_release0_immutable_candidate(
                self._document(
                    term_contract={
                        **term,
                        "api_background_jobs_enabled": False,
                        "lease_duration_seconds": 61,
                    }
                ),
                authority=self.authority,
            )
        with self.assertRaisesRegex(subject.Release0ImmutableCandidateError, "TERM_CONTRACT_INVALID"):
            subject.verify_release0_immutable_candidate(
                self._document(
                    term_contract={
                        **term,
                        "api_background_jobs_enabled": False,
                        "lease_duration_seconds": 60.0,
                    }
                ),
                authority=self.authority,
            )
        with self.assertRaisesRegex(subject.Release0ImmutableCandidateError, "COMPOSE_INVALID"):
            subject.verify_release0_immutable_candidate(
                self._document(
                    compose={
                        "fi_writer_relative_path": "deploy/production/docker-compose.webapp-fi-writer-2c08.yml",
                        "fi_writer_sha256": "3" * 64,
                        "ir_promoted_relative_path": "deploy/production/docker-compose.webapp-ir-promoted-release0.yml",
                        "ir_promoted_sha256": "4" * 64,
                    }
                ),
                authority=self.authority,
            )

    def test_exact_critical_source_set_candidate_id_and_images_are_required(self) -> None:
        source_hashes = {
            path: hashlib.sha256(path.encode("ascii")).hexdigest()
            for path in subject.CANDIDATE_CRITICAL_SOURCE_FILES
        }
        source_hashes.pop("core/sms.py")
        with self.assertRaisesRegex(subject.Release0ImmutableCandidateError, "CRITICAL_SOURCE_FILES_INVALID"):
            subject.verify_release0_immutable_candidate(
                self._document(critical_source_files=source_hashes), authority=self.authority
            )
        with self.assertRaisesRegex(subject.Release0ImmutableCandidateError, "CANDIDATE_ID_INVALID"):
            subject.verify_release0_immutable_candidate(
                self._document(candidate_id="release0-" + "0" * 12 + "-" + "b" * 12),
                authority=self.authority,
            )
        with self.assertRaisesRegex(subject.Release0ImmutableCandidateError, "IMAGE_INVALID"):
            subject.verify_release0_immutable_candidate(
                self._document(
                    images={
                        "app": {
                            "image_repo_digest": "registry.invalid/release0-app:latest",
                            "image_id": "sha256:" + "1" * 64,
                        },
                        "bot": {
                            "image_repo_digest": "registry.invalid/release0-bot@sha256:" + "f" * 64,
                            "image_id": "sha256:" + "2" * 64,
                        },
                    }
                ),
                authority=self.authority,
            )

    def test_signature_canonicalization_and_mutation_are_fail_closed(self) -> None:
        document = self._document()
        changed = document.replace(b'"fi_writer_sha256":"' + b"3" * 64, b'"fi_writer_sha256":"' + b"5" * 64)
        with self.assertRaisesRegex(subject.Release0ImmutableCandidateError, "SIGNATURE_INVALID"):
            subject.verify_release0_immutable_candidate(changed, authority=self.authority)
        noncanonical = document.replace(b',"signature_base64"', b', "signature_base64"')
        with self.assertRaisesRegex(subject.Release0ImmutableCandidateError, "CANONICAL_REQUIRED"):
            subject.verify_release0_immutable_candidate(noncanonical, authority=self.authority)
        with self.assertRaisesRegex(subject.Release0ImmutableCandidateError, "DUPLICATE_FIELD"):
            subject.verify_release0_immutable_candidate(
                b'{"schema":"x","schema":"x"}', authority=self.authority
            )
        candidate = subject.verify_release0_immutable_candidate(document, authority=self.authority)
        with self.assertRaisesRegex(TypeError, "COPY_FORBIDDEN"):
            copy.copy(candidate)
        object.__setattr__(candidate.application, "release_sha", "0" * 40)
        with self.assertRaisesRegex(subject.Release0ImmutableCandidateError, "UNVERIFIED"):
            subject.require_verified_release0_immutable_candidate(candidate)

    def test_deep_json_is_a_typed_refusal(self) -> None:
        document = b'{"schema":' + (b"[" * 1200) + b"0" + (b"]" * 1200) + b"}"
        with self.assertRaisesRegex(
            subject.Release0ImmutableCandidateError,
            "DOCUMENT_INVALID|FIELDS_INVALID",
        ):
            subject.verify_release0_immutable_candidate(document, authority=self.authority)
