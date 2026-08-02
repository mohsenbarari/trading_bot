"""Focused local-only tests for the Release-0 v2 descriptor builder."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import stat
import subprocess
import tempfile
from unittest import TestCase, mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import fenced_fi_release_identity as identity_contract
from core import term_fenced_application_capability as application_capability
from scripts import build_fenced_fi_release_identity as subject
from scripts import verify_term_fenced_application_source as source_verifier


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_IMAGE = "registry.example.invalid/trading-bot-app:release0"
BOT_IMAGE = "registry.example.invalid/trading-bot-bot:release0"
APP_DIGEST = "registry.example.invalid/trading-bot-app@sha256:" + "a" * 64
BOT_DIGEST = "registry.example.invalid/trading-bot-bot@sha256:" + "b" * 64
APP_ID = "sha256:" + "c" * 64
BOT_ID = "sha256:" + "d" * 64


def _run(*arguments: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(
        ["/usr/bin/git", *arguments],
        cwd=cwd,
        text=True,
    ).strip()


def _write(path: Path, payload: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)


def _new_git_repository(path: Path) -> None:
    _run("init", "-q", str(path))
    _run("-C", str(path), "config", "user.email", "release0-test@example.invalid")
    _run("-C", str(path), "config", "user.name", "Release Zero Test")


def _commit(path: Path, message: str) -> str:
    _run("-C", str(path), "add", ".")
    _run("-C", str(path), "commit", "-qm", message)
    return _run("-C", str(path), "rev-parse", "HEAD")


class DescriptorBuilderFixture:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.inputs = directory / "inputs"
        self.inputs.mkdir(mode=0o700)
        self.source_root = self._make_source_release()
        self.control_root = self._make_control_release()
        self.evidence_path = self.inputs / "term-fenced-evidence.json"
        source_tree = source_verifier.load_clean_source_tree(self.source_root)
        _write(
            self.evidence_path,
            source_verifier.build_evidence(source_tree),
            mode=0o600,
        )
        self.private = Ed25519PrivateKey.generate()
        self.private_path = self.inputs / "release-identity.key"
        _write(
            self.private_path,
            self.private.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            ),
            mode=0o600,
        )
        self.authority_path = self.inputs / "release-identity-authority.pub"
        self.public = self.private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        _write(self.authority_path, base64.b64encode(self.public) + b"\n", mode=0o600)
        self.output = self.inputs / "fenced-release-identity.json"

    def _make_source_release(self) -> Path:
        work = self.directory / "source-work"
        _new_git_repository(work)
        for relative in application_capability.TERM_FENCED_APPLICATION_CAPABILITY_FILES:
            _write(
                work / relative,
                subprocess.check_output(
                    ["/usr/bin/git", "show", f"HEAD:{relative}"], cwd=REPO_ROOT
                ),
            )
        sha = _commit(work, "term fenced application source")
        root = self.directory / "application" / sha
        root.parent.mkdir()
        work.rename(root)
        return root

    def _make_control_release(self) -> Path:
        work = self.directory / "control-work"
        _new_git_repository(work)
        relative = subject.FENCED_COMPOSE_RELATIVE_PATH
        _write(
            work / relative,
            (REPO_ROOT / relative).read_bytes(),
        )
        sha = _commit(work, "fenced writer control release")
        root = self.directory / "control" / sha
        root.parent.mkdir()
        work.rename(root)
        return root

    @property
    def authority(self) -> identity_contract.FencedFiReleaseIdentityAuthority:
        return identity_contract.FencedFiReleaseIdentityAuthority(
            public_key=self.public,
            key_id="ed25519-sha256:" + hashlib.sha256(self.public).hexdigest(),
        )

    def labels(self) -> dict[str, str]:
        evidence = application_capability.verify_term_fenced_application_capability(
            self.evidence_path.read_bytes()
        )
        return application_capability.expected_term_fenced_image_labels(evidence)

    def image_metadata(self, reference: str) -> dict[str, object]:
        if reference == APP_IMAGE:
            return {
                "Id": APP_ID,
                "RepoDigests": [APP_DIGEST],
                "Config": {"Labels": self.labels()},
            }
        if reference == BOT_IMAGE:
            return {
                "Id": BOT_ID,
                "RepoDigests": [BOT_DIGEST],
                "Config": {"Labels": self.labels()},
            }
        raise AssertionError(f"unexpected local image reference: {reference}")

    def build(self) -> subject.BuiltFencedFiReleaseIdentity:
        return subject.build_fenced_fi_release_identity(
            application_release_root=self.source_root,
            control_release_root=self.control_root,
            term_fenced_application_evidence=self.evidence_path,
            app_image=APP_IMAGE,
            app_repo_digest=APP_DIGEST,
            bot_image=BOT_IMAGE,
            bot_repo_digest=BOT_DIGEST,
            signing_private_key=self.private_path,
            authority_public_key=self.authority_path,
        )


class BuildFencedFiReleaseIdentityTests(TestCase):
    def test_docker_inventory_forces_the_local_unix_socket(self) -> None:
        inspected = {
            "Id": APP_ID,
            "RepoDigests": [APP_DIGEST],
            "Config": {"Labels": {}},
        }
        payload = (
            b'{"Config":{"Labels":{}},"Id":"'
            + APP_ID.encode("ascii")
            + b'","RepoDigests":["'
            + APP_DIGEST.encode("ascii")
            + b'"]}'
        )
        with (
            mock.patch.object(
                subject,
                "_trusted_executable",
                return_value=Path("/usr/bin/docker"),
            ),
            mock.patch.object(
                subject,
                "_run_bounded_command",
                return_value=payload,
            ) as runner,
        ):
            self.assertEqual(inspected, subject._run_docker_image_inspect(APP_IMAGE))
        command = runner.call_args.args[0]
        self.assertEqual(
            ["/usr/bin/docker", "--host", subject.LOCAL_DOCKER_SOCKET],
            command[:3],
        )
        self.assertEqual("image", command[3])
        self.assertNotIn("DOCKER_HOST", runner.call_args.kwargs["env"])
        self.assertNotIn("DOCKER_CONTEXT", runner.call_args.kwargs["env"])

    def test_bounded_command_rejects_excess_output_without_a_pipe_buffer(self) -> None:
        with self.assertRaisesRegex(
            subject.BuildFencedFiReleaseIdentityError,
            "TEST_REJECTED",
        ):
            subject._run_bounded_command(
                ["/usr/bin/printf", "abcdef"],
                env={"PATH": "/usr/bin:/bin"},
                cwd=None,
                maximum_bytes=3,
                unavailable_code="TEST_UNAVAILABLE",
                rejected_code="TEST_REJECTED",
            )

    def test_builds_canonical_signed_v2_descriptor_from_local_facts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = DescriptorBuilderFixture(Path(raw))
            with mock.patch.object(
                subject,
                "_run_docker_image_inspect",
                side_effect=fixture.image_metadata,
            ):
                built = fixture.build()
            subject.write_new_fenced_fi_release_identity(
                fixture.output,
                payload=built.document,
            )

            verified = identity_contract.verify_fenced_fi_release_identity(
                fixture.output.read_bytes(), authority=fixture.authority
            )
            self.assertIs(
                verified,
                identity_contract.require_term_fenced_fi_release_candidate(verified),
            )
            self.assertEqual(_run("-C", str(fixture.source_root), "rev-parse", "HEAD"), verified.release_sha)
            self.assertEqual(_run("-C", str(fixture.control_root), "rev-parse", "HEAD"), verified.control_release_sha)
            self.assertEqual(str(fixture.source_root), verified.application_release_root)
            self.assertEqual(str(fixture.control_root), verified.control_release_root)
            self.assertEqual(
                hashlib.sha256(
                    (fixture.control_root / subject.FENCED_COMPOSE_RELATIVE_PATH).read_bytes()
                ).hexdigest(),
                verified.compose_sha256,
            )
            self.assertEqual(APP_DIGEST, verified.app_image_repo_digest)
            self.assertEqual(APP_ID, verified.app_image_id)
            self.assertEqual(BOT_DIGEST, verified.bot_image_repo_digest)
            self.assertEqual(BOT_ID, verified.bot_image_id)
            self.assertEqual(
                hashlib.sha256(fixture.evidence_path.read_bytes()).hexdigest(),
                verified.term_fenced_application_evidence_sha256,
            )
            self.assertEqual(0o600, stat.S_IMODE(fixture.output.stat().st_mode))
            self.assertFalse(fixture.output.read_bytes().endswith(b"\n"))

    def test_builder_uses_its_bounded_git_loader_before_pure_evidence_verification(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = DescriptorBuilderFixture(Path(raw))
            with (
                mock.patch.object(
                    subject,
                    "_run_docker_image_inspect",
                    side_effect=fixture.image_metadata,
                ),
                mock.patch.object(
                    subject.source_verifier,
                    "load_clean_source_tree",
                    side_effect=AssertionError("unbounded shared Git loader must not be used"),
                ),
            ):
                built = fixture.build()
            self.assertEqual(
                _run("-C", str(fixture.source_root), "rev-parse", "HEAD"),
                built.source.release_sha,
            )

    def test_refuses_selected_digest_not_present_in_local_docker_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = DescriptorBuilderFixture(Path(raw))
            wrong = fixture.image_metadata(APP_IMAGE)
            wrong["RepoDigests"] = [
                "registry.example.invalid/trading-bot-app@sha256:" + "f" * 64
            ]

            def inspected(reference: str) -> dict[str, object]:
                return wrong if reference == APP_IMAGE else fixture.image_metadata(reference)

            with mock.patch.object(subject, "_run_docker_image_inspect", side_effect=inspected):
                with self.assertRaisesRegex(
                    subject.BuildFencedFiReleaseIdentityError,
                    "APP_REPO_DIGEST_NOT_LOCAL",
                ):
                    fixture.build()
            self.assertFalse(fixture.output.exists())

    def test_refuses_image_without_matching_term_fence_labels(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = DescriptorBuilderFixture(Path(raw))
            wrong = fixture.image_metadata(BOT_IMAGE)
            wrong["Config"] = {"Labels": {"org.opencontainers.image.revision": "0" * 40}}

            def inspected(reference: str) -> dict[str, object]:
                return wrong if reference == BOT_IMAGE else fixture.image_metadata(reference)

            with mock.patch.object(subject, "_run_docker_image_inspect", side_effect=inspected):
                with self.assertRaisesRegex(
                    subject.BuildFencedFiReleaseIdentityError,
                    "BOT_IMAGE_LABEL_MISMATCH",
                ):
                    fixture.build()

    def test_hard_blocks_legacy_2c08_before_control_or_image_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = DescriptorBuilderFixture(Path(raw))
            legacy = subject.SourceRelease(
                root=fixture.source_root,
                release_sha=subject.LEGACY_UNFENCED_APPLICATION_RELEASE_SHA,
                release_tree_sha="0" * 40,
                evidence_sha256="1" * 64,
            )
            with (
                mock.patch.object(
                    subject,
                    "_load_source_release",
                    return_value=legacy,
                ),
                mock.patch.object(subject, "_load_control_release") as control,
                mock.patch.object(subject, "_inspect_local_image") as image,
                self.assertRaisesRegex(
                    subject.BuildFencedFiReleaseIdentityError,
                    "LEGACY_2C08_APPLICATION_BLOCKED",
                ),
            ):
                fixture.build()

            control.assert_not_called()
            image.assert_not_called()

    def test_refuses_private_key_that_does_not_match_pinned_authority(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = DescriptorBuilderFixture(Path(raw))
            other = Ed25519PrivateKey.generate().public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            _write(fixture.authority_path, base64.b64encode(other) + b"\n", mode=0o600)
            with self.assertRaisesRegex(
                subject.BuildFencedFiReleaseIdentityError,
                "SIGNING_KEY_AUTHORITY_MISMATCH",
            ):
                fixture.build()

    def test_detects_a_local_image_identity_change_between_observations(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = DescriptorBuilderFixture(Path(raw))
            app_first = fixture.image_metadata(APP_IMAGE)
            app_second = fixture.image_metadata(APP_IMAGE)
            app_second["Id"] = "sha256:" + "e" * 64
            sequence = [
                app_first,
                fixture.image_metadata(BOT_IMAGE),
                app_second,
                fixture.image_metadata(BOT_IMAGE),
            ]
            with mock.patch.object(
                subject,
                "_run_docker_image_inspect",
                side_effect=sequence,
            ):
                with self.assertRaisesRegex(
                    subject.BuildFencedFiReleaseIdentityError,
                    "LOCAL_IMAGE_CHANGED",
                ):
                    fixture.build()

    def test_output_is_create_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = DescriptorBuilderFixture(Path(raw))
            with mock.patch.object(
                subject,
                "_run_docker_image_inspect",
                side_effect=fixture.image_metadata,
            ):
                built = fixture.build()
            subject.write_new_fenced_fi_release_identity(fixture.output, payload=built.document)
            original = fixture.output.read_bytes()
            with self.assertRaisesRegex(
                subject.BuildFencedFiReleaseIdentityError,
                "OUTPUT_EXISTS",
            ):
                subject.write_new_fenced_fi_release_identity(fixture.output, payload=b"different")
            self.assertEqual(original, fixture.output.read_bytes())

    def test_short_write_cleans_only_its_temporary_inode_and_never_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = DescriptorBuilderFixture(Path(raw))
            with mock.patch.object(subject.os, "write", return_value=0):
                with self.assertRaisesRegex(
                    subject.BuildFencedFiReleaseIdentityError,
                    "OUTPUT_WRITE_FAILED",
                ):
                    subject.write_new_fenced_fi_release_identity(
                        fixture.output,
                        payload=b"descriptor",
                    )
            self.assertFalse(fixture.output.exists())
            self.assertEqual([], list(fixture.inputs.glob(".fenced-release-identity.json.tmp-*")))

    def test_file_fsync_failure_never_publishes_the_final_name(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = DescriptorBuilderFixture(Path(raw))
            with mock.patch.object(subject.os, "fsync", side_effect=OSError("injected")):
                with self.assertRaisesRegex(
                    subject.BuildFencedFiReleaseIdentityError,
                    "OUTPUT_UNAVAILABLE",
                ):
                    subject.write_new_fenced_fi_release_identity(
                        fixture.output,
                        payload=b"descriptor",
                    )
            self.assertFalse(fixture.output.exists())
            self.assertEqual([], list(fixture.inputs.glob(".fenced-release-identity.json.tmp-*")))

    def test_rejects_a_nonsticky_writable_ancestor_before_reading_signing_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = DescriptorBuilderFixture(Path(raw))
            fixture.inputs.chmod(0o777)
            with self.assertRaisesRegex(
                subject.BuildFencedFiReleaseIdentityError,
                "TERM_FENCED_APPLICATION_EVIDENCE_ANCESTOR_UNSAFE",
            ):
                fixture.build()


if __name__ == "__main__":
    import unittest

    unittest.main()
