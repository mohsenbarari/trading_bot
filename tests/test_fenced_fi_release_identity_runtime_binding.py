"""Tests for the inert WA-FI release identity/runtime equality seam."""

from __future__ import annotations

import base64
import copy
import hashlib
from dataclasses import replace
from unittest import TestCase

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import fenced_fi_release_identity as identity_subject
from core import fenced_fi_release_identity_runtime_binding as subject


class FencedFiReleaseIdentityRuntimeBindingTests(TestCase):
    def setUp(self) -> None:
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        authority = identity_subject.FencedFiReleaseIdentityAuthority(
            public_key=public,
            key_id="ed25519-sha256:" + hashlib.sha256(public).hexdigest(),
        )
        unsigned: dict[str, object] = {
            "schema": identity_subject.FENCED_FI_RELEASE_IDENTITY_SCHEMA,
            "release_sha": "a" * 40,
            "release_tree_sha": "b" * 40,
            "application_release_root": "/srv/releases/" + "a" * 40,
            "control_release_sha": "c" * 40,
            "control_release_tree_sha": "d" * 40,
            "control_release_root": "/srv/control/" + "c" * 40,
            "compose_relative_path": "deploy/production/docker-compose.webapp-fi-writer-release-v1.yml",
            "compose_sha256": "e" * 64,
            "services": {
                "app": {
                    "image_repo_digest": "registry.invalid/app@sha256:" + "f" * 64,
                    "image_id": "sha256:" + "1" * 64,
                },
                "bot": {
                    "image_repo_digest": "registry.invalid/bot@sha256:" + "2" * 64,
                    "image_id": "sha256:" + "3" * 64,
                },
            },
            "signer_key_id": authority.key_id,
        }
        signature = private.sign(
            b"gold-trade-wa-fi-fenced-release-identity-v1\x00"
            + identity_subject.canonical_fenced_fi_release_identity_json_bytes(unsigned)
        )
        signed = dict(unsigned)
        signed["signature_base64"] = base64.b64encode(signature).decode("ascii")
        self.identity = identity_subject.verify_fenced_fi_release_identity(
            identity_subject.canonical_fenced_fi_release_identity_json_bytes(signed), authority=authority
        )
        self.observations = subject.FencedFiReleaseIdentityRuntimeObservations(
            application_release_root=self.identity.application_release_root,
            control_release_root=self.identity.control_release_root,
            compose_relative_path=self.identity.compose_relative_path,
            compose_sha256=self.identity.compose_sha256,
            app_image_repo_digest=self.identity.app_image_repo_digest,
            app_image_id=self.identity.app_image_id,
            bot_image_repo_digest=self.identity.bot_image_repo_digest,
            bot_image_id=self.identity.bot_image_id,
        )

    def test_exact_observations_bind_but_grant_no_authority(self) -> None:
        binding = subject.bind_fenced_fi_release_identity_runtime(
            self.identity, observations=self.observations
        )
        self.assertEqual(subject.FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_SCHEMA, binding.schema)
        self.assertEqual("equality-evidence-only", binding.status)
        self.assertEqual(self.identity.identity_sha256, binding.identity_sha256)
        self.assertRegex(binding.binding_sha256, r"^[0-9a-f]{64}$")
        self.assertFalse(binding.writer_authorized)
        self.assertFalse(binding.promotion_authorized)
        self.assertFalse(binding.deployment_authorized)
        self.assertFalse(binding.execution_authorized)
        self.assertFalse(binding.full_matrix_authorized)
        self.assertFalse(binding.full_matrix_executed)
        self.assertIs(binding, subject.require_bound_fenced_fi_release_identity_runtime(binding))

    def test_each_root_compose_and_image_observation_mismatch_refuses(self) -> None:
        cases = (
            ("application_release_root", "/srv/releases/" + "f" * 40, "APPLICATION_ROOT_MISMATCH"),
            ("control_release_root", "/srv/control/" + "f" * 40, "CONTROL_ROOT_MISMATCH"),
            ("compose_relative_path", "deploy/production/docker-compose.webapp-fi-writer-other.yml", "COMPOSE_PATH_MISMATCH"),
            ("compose_sha256", "a" * 64, "COMPOSE_SHA256_MISMATCH"),
            ("app_image_repo_digest", "registry.invalid/app@sha256:" + "a" * 64, "APP_REPO_DIGEST_MISMATCH"),
            ("app_image_id", "sha256:" + "a" * 64, "APP_IMAGE_ID_MISMATCH"),
            ("bot_image_repo_digest", "registry.invalid/bot@sha256:" + "a" * 64, "BOT_REPO_DIGEST_MISMATCH"),
            ("bot_image_id", "sha256:" + "a" * 64, "BOT_IMAGE_ID_MISMATCH"),
        )
        for field_name, changed, code in cases:
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(
                    subject.FencedFiReleaseIdentityRuntimeBindingError, code
                ):
                    subject.bind_fenced_fi_release_identity_runtime(
                        self.identity,
                        observations=replace(self.observations, **{field_name: changed}),
                    )

    def test_lookalikes_and_authorizing_identity_are_refused(self) -> None:
        with self.assertRaisesRegex(
            subject.FencedFiReleaseIdentityRuntimeBindingError, "IDENTITY_INVALID"
        ):
            subject.bind_fenced_fi_release_identity_runtime(object(), observations=self.observations)
        with self.assertRaisesRegex(
            subject.FencedFiReleaseIdentityRuntimeBindingError, "OBSERVATIONS_INVALID"
        ):
            subject.bind_fenced_fi_release_identity_runtime(self.identity, observations=object())
        with self.assertRaisesRegex(
            subject.FencedFiReleaseIdentityRuntimeBindingError, "IDENTITY_INVALID"
        ):
            subject.bind_fenced_fi_release_identity_runtime(
                replace(self.identity, writer_authorized=True), observations=self.observations
            )
        with self.assertRaisesRegex(
            subject.FencedFiReleaseIdentityRuntimeBindingError, "IDENTITY_INVALID"
        ):
            subject.bind_fenced_fi_release_identity_runtime(
                replace(self.identity, identity_sha256="0" * 64), observations=self.observations
            )

    def test_forged_or_copied_binding_is_not_accepted(self) -> None:
        binding = subject.bind_fenced_fi_release_identity_runtime(
            self.identity, observations=self.observations
        )
        forged = subject.FencedFiReleaseIdentityRuntimeBinding(
            schema=binding.schema,
            status=binding.status,
            binding_sha256=binding.binding_sha256,
            identity_sha256=binding.identity_sha256,
            application_release_root=binding.application_release_root,
            control_release_root=binding.control_release_root,
            compose_relative_path=binding.compose_relative_path,
            compose_sha256=binding.compose_sha256,
            app_image_repo_digest=binding.app_image_repo_digest,
            app_image_id=binding.app_image_id,
            bot_image_repo_digest=binding.bot_image_repo_digest,
            bot_image_id=binding.bot_image_id,
        )
        for value in (forged,):
            with self.assertRaisesRegex(
                subject.FencedFiReleaseIdentityRuntimeBindingError, "BINDING_INVALID"
            ):
                subject.require_bound_fenced_fi_release_identity_runtime(value)
        with self.assertRaisesRegex(TypeError, "COPY_FORBIDDEN"):
            copy.copy(binding)
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            binding.__reduce_ex__(4)

    def test_post_bind_mutation_is_refused(self) -> None:
        binding = subject.bind_fenced_fi_release_identity_runtime(
            self.identity, observations=self.observations
        )
        object.__setattr__(binding, "application_release_root", "/attacker")
        with self.assertRaisesRegex(
            subject.FencedFiReleaseIdentityRuntimeBindingError, "BINDING_INVALID"
        ):
            subject.require_bound_fenced_fi_release_identity_runtime(binding)

    def test_source_identity_mutation_after_bind_is_refused(self) -> None:
        binding = subject.bind_fenced_fi_release_identity_runtime(
            self.identity, observations=self.observations
        )
        object.__setattr__(self.identity, "compose_sha256", "0" * 64)
        with self.assertRaisesRegex(
            subject.FencedFiReleaseIdentityRuntimeBindingError, "BINDING_INVALID"
        ):
            subject.require_bound_fenced_fi_release_identity_runtime(binding)
