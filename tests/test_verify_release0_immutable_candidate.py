from __future__ import annotations

import ast
import base64
import hashlib
import os
from pathlib import Path
import tempfile
from unittest import TestCase, mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import release0_immutable_candidate as contract
from scripts import verify_release0_immutable_candidate as subject


def _write_private(path: Path, value: bytes) -> None:
    path.write_bytes(value)
    path.chmod(0o600)


class VerifyRelease0ImmutableCandidateTests(TestCase):
    def _fixture(self, directory: Path) -> tuple[Path, Path, str]:
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        authority_path = directory / "authority.pub"
        _write_private(authority_path, base64.b64encode(public) + b"\n")
        application_sha = "a" * 40
        control_sha = "b" * 40
        unsigned: dict[str, object] = {
            "schema": contract.RELEASE0_IMMUTABLE_CANDIDATE_SCHEMA,
            "candidate_id": "release0-" + application_sha[:12] + "-" + control_sha[:12],
            "application": {
                "release_sha": application_sha,
                "tree_sha": "c" * 40,
                "release_root": "/srv/releases/" + application_sha,
            },
            "control": {
                "release_sha": control_sha,
                "tree_sha": "d" * 40,
                "release_root": "/srv/control/" + control_sha,
            },
            "images": {
                "app": {
                    "image_repo_digest": "registry.invalid/app@sha256:" + "e" * 64,
                    "image_id": "sha256:" + "1" * 64,
                },
                "bot": {
                    "image_repo_digest": "registry.invalid/bot@sha256:" + "f" * 64,
                    "image_id": "sha256:" + "2" * 64,
                },
            },
            "term_contract": {
                "schema": contract.RELEASE0_TERM_CONTRACT_SCHEMA,
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
                for path in contract.CANDIDATE_CRITICAL_SOURCE_FILES
            },
            "compose": {
                "fi_writer_relative_path": "deploy/production/docker-compose.webapp-fi-writer-release0.yml",
                "fi_writer_sha256": "3" * 64,
                "ir_promoted_relative_path": "deploy/production/docker-compose.webapp-ir-promoted-release0.yml",
                "ir_promoted_sha256": "4" * 64,
            },
            "signer_key_id": "ed25519-sha256:" + hashlib.sha256(public).hexdigest(),
        }
        signature = private.sign(
            b"gold-trade-release0-immutable-candidate-v1\x00"
            + contract.canonical_release0_immutable_candidate_json_bytes(unsigned)
        )
        document = dict(unsigned)
        document["signature_base64"] = base64.b64encode(signature).decode("ascii")
        descriptor_path = directory / "candidate.json"
        descriptor = contract.canonical_release0_immutable_candidate_json_bytes(document)
        _write_private(descriptor_path, descriptor)
        return descriptor_path, authority_path, hashlib.sha256(descriptor).hexdigest()

    def test_descriptor_verification_is_explicitly_non_authorizing(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.object(os, "geteuid", return_value=0):
            descriptor, authority, expected = self._fixture(Path(raw))
            result = subject.verify(
                descriptor_path=descriptor,
                authority_path=authority,
                expected_candidate_sha256=expected,
                check_local_roots=False,
            )

        self.assertEqual("verified-descriptor-non-authorizing", result["status"])
        self.assertFalse(result["writer_authorized"])
        self.assertFalse(result["promotion_authorized"])
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["full_matrix_authorized"])

    def test_expected_descriptor_hash_and_file_mode_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.object(os, "geteuid", return_value=0):
            descriptor, authority, expected = self._fixture(Path(raw))
            with self.assertRaisesRegex(
                subject.VerifyRelease0ImmutableCandidateError,
                "expected hash",
            ):
                subject.verify(
                    descriptor_path=descriptor,
                    authority_path=authority,
                    expected_candidate_sha256="0" * 64,
                    check_local_roots=False,
                )
            authority.chmod(0o666)
            with self.assertRaisesRegex(
                subject.VerifyRelease0ImmutableCandidateError,
                "owner-controlled",
            ):
                subject.verify(
                    descriptor_path=descriptor,
                    authority_path=authority,
                    expected_candidate_sha256=expected,
                    check_local_roots=False,
                )

    def test_local_validation_reads_only_the_signed_clean_roots(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.object(os, "geteuid", return_value=0):
            descriptor, authority, expected = self._fixture(Path(raw))
            candidate = subject.load_verified_release0_immutable_candidate(
                descriptor_path=descriptor,
                authority_path=authority,
                expected_candidate_sha256=expected,
            )
            with mock.patch.object(subject, "_validate_clean_git_release") as git_check, mock.patch.object(
                subject, "_validate_file_hash"
            ) as hash_check:
                result = subject.verify_local_release0_immutable_candidate(candidate)

        self.assertEqual("verified-local-non-authorizing", result["status"])
        self.assertEqual(2, git_check.call_count)
        self.assertEqual(len(contract.CANDIDATE_CRITICAL_SOURCE_FILES) + 2, hash_check.call_count)
        self.assertEqual("Release-0 application release", git_check.call_args_list[0].kwargs["label"])
        self.assertEqual("Release-0 control release", git_check.call_args_list[1].kwargs["label"])
        relative_paths = [call.kwargs["relative"] for call in hash_check.call_args_list]
        self.assertEqual(list(contract.CANDIDATE_CRITICAL_SOURCE_FILES), relative_paths[:-2])
        self.assertEqual(
            [
                "deploy/production/docker-compose.webapp-fi-writer-release0.yml",
                "deploy/production/docker-compose.webapp-ir-promoted-release0.yml",
            ],
            relative_paths[-2:],
        )

    def test_local_verifier_has_no_activation_or_remote_transport_surface(self) -> None:
        source_path = Path(subject.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imported_modules = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertTrue({"socket", "urllib", "httpx", "boto3", "requests"}.isdisjoint(imported_modules))
        string_literals = {
            node.value.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        for forbidden in (
            "/usr/bin/docker",
            "systemctl",
            "ssh",
            "scp",
            "rsync",
            "boto3",
            "s3://",
        ):
            self.assertNotIn(forbidden, string_literals)

    def test_platform_without_no_follow_is_rejected_before_open(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.object(os, "geteuid", return_value=0):
            descriptor, authority, expected = self._fixture(Path(raw))
            with mock.patch.object(subject.os, "O_NOFOLLOW", create=True, new=None):
                with self.assertRaisesRegex(
                    subject.VerifyRelease0ImmutableCandidateError,
                    "O_NOFOLLOW",
                ):
                    subject.verify(
                        descriptor_path=descriptor,
                        authority_path=authority,
                        expected_candidate_sha256=expected,
                        check_local_roots=False,
                    )
