import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/prepare_writer_witness_immutable_release.py"
SPEC = importlib.util.spec_from_file_location("prepare_writer_witness_immutable_release", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release_package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_package)


def agent_config(*, duration: int = 60) -> dict[str, object]:
    return {
        "schema": "production-writer-lease-agent-v1",
        "mode": "fenced_fi_writer",
        "site": "webapp_fi",
        "lease_file": "/var/lib/trading-bot-three-site/writer-terms/writer-lease.json",
        "runtime": {
            "compose_file": "/srv/trading-bot-three-site/control-releases/example/deploy/production/docker-compose.webapp-fi-writer-2c08.yml",
            "env_file": "/root/secure-envs/trading-bot/wa-fi-fenced-writer-runtime.env",
            "selection_env_file": None,
            "services": ["app", "bot"],
        },
        "witness": {
            "url": "https://witness.example.invalid",
            "key_id": "webapp-fi-key",
            "secret_file": "/root/secure-envs/trading-bot/webapp-fi-witness.secret",
            "public_key_file": "/root/secure-envs/trading-bot/witness-public.key",
            "ca_bundle": "/root/secure-envs/trading-bot/witness-ca.pem",
            "timeout_seconds": 3,
            "lease_duration_seconds": duration,
            "safety_margin_seconds": 15,
            "renew_interval_seconds": 10,
        },
    }


class WriterWitnessImmutableReleaseTests(unittest.TestCase):
    def test_normal_worktree_git_pointer_resolves_to_a_root_controlled_git_directory(self):
        git_directory = release_package._resolve_worktree_git_directory(ROOT)

        metadata = git_directory.stat()
        self.assertTrue(git_directory.is_dir())
        self.assertEqual(metadata.st_uid, 0)
        self.assertFalse(metadata.st_mode & 0o022)

    def test_source_worktree_rejects_group_or_other_writable_directory(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-source-insecure-") as value:
            worktree = Path(value) / "worktree"
            worktree.mkdir(mode=0o777)
            worktree.chmod(0o777)

            with self.assertRaisesRegex(
                release_package.WitnessReleasePreparationError,
                "source worktree is not root-owned",
            ):
                release_package._require_source_repository(worktree)

    def test_git_pointer_rejects_a_group_or_other_writable_target(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-git-pointer-") as value:
            root = Path(value)
            worktree = root / "worktree"
            git_directory = root / "git-directory"
            worktree.mkdir(mode=0o700)
            git_directory.mkdir(mode=0o777)
            git_directory.chmod(0o777)
            pointer = worktree / ".git"
            pointer.write_text(f"gitdir: {git_directory}\n", encoding="utf-8")
            pointer.chmod(0o600)

            with self.assertRaisesRegex(
                release_package.WitnessReleasePreparationError,
                "resolved source Git directory is not root-owned",
            ):
                release_package._resolve_worktree_git_directory(worktree)

    def test_git_pointer_file_must_not_be_group_or_other_writable(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-git-pointer-mode-") as value:
            root = Path(value)
            worktree = root / "worktree"
            git_directory = root / "git-directory"
            worktree.mkdir(mode=0o700)
            git_directory.mkdir(mode=0o700)
            pointer = worktree / ".git"
            pointer.write_text(f"gitdir: {git_directory}\n", encoding="utf-8")
            pointer.chmod(0o666)

            with self.assertRaisesRegex(
                release_package.WitnessReleasePreparationError,
                r"source worktree \.git pointer (is writable|has unsafe ownership)",
            ):
                release_package._resolve_worktree_git_directory(worktree)

    def test_prepare_creates_a_detached_hash_bound_source_package(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-package-") as value:
            destination = Path(value) / "package"
            result = release_package.prepare_release_package(
                source_repository=ROOT,
                destination=destination,
            )

            self.assertEqual(result["status"], "prepared")
            self.assertEqual(result["source_commit"], release_package.PINNED_SOURCE_COMMIT)
            self.assertTrue(destination.is_dir())
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)

            manifest = json.loads((destination / "release-package.json").read_text())
            self.assertEqual(manifest["schema"], release_package.PACKAGE_SCHEMA)
            self.assertEqual(manifest["source"]["commit"], release_package.PINNED_SOURCE_COMMIT)
            archive = destination / manifest["source"]["archive"]["name"]
            self.assertEqual(
                hashlib.sha256(archive.read_bytes()).hexdigest(),
                manifest["source"]["archive"]["sha256"],
            )
            self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE((destination / "release-package.json").stat().st_mode),
                0o600,
            )

            with tarfile.open(archive, "r:") as handle:
                app = handle.extractfile("writer-witness-source/writer_witness_app.py")
                self.assertIsNotNone(app)
                source = app.read()
            self.assertIn(b"writer_witness_enforce_configured_lease_duration", source)
            self.assertEqual(
                hashlib.sha256(source).hexdigest(),
                manifest["source"]["required_files"]["writer_witness_app.py"],
            )

    def test_prepare_refuses_to_overwrite_a_destination(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-package-existing-") as value:
            destination = Path(value) / "package"
            destination.mkdir()
            with self.assertRaisesRegex(
                release_package.WitnessReleasePreparationError,
                "must not already exist",
            ):
                release_package.prepare_release_package(
                    source_repository=ROOT,
                    destination=destination,
                )

    def test_destination_parent_must_be_root_private(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-parent-mode-") as value:
            parent = Path(value) / "shared-parent"
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)

            with self.assertRaisesRegex(
                release_package.WitnessReleasePreparationError,
                "destination parent is not root-owned",
            ):
                release_package._require_new_directory(parent / "package")

    def test_destination_parent_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-parent-link-") as value:
            root = Path(value)
            actual_parent = root / "actual-parent"
            actual_parent.mkdir(mode=0o700)
            link_parent = root / "linked-parent"
            link_parent.symlink_to(actual_parent, target_is_directory=True)

            with self.assertRaisesRegex(
                release_package.WitnessReleasePreparationError,
                "destination parent must be one canonical non-symlink",
            ):
                release_package._require_new_directory(link_parent / "package")

    def test_failed_new_file_is_preserved_for_forensics(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-file-preserve-") as value:
            artifact = Path(value) / "artifact.json"
            with mock.patch.object(release_package.os, "fsync", side_effect=OSError("disk failed")):
                with self.assertRaises(OSError):
                    release_package._write_new_file(artifact, b"partial-evidence")

            self.assertTrue(artifact.exists())
            self.assertEqual(artifact.read_bytes(), b"partial-evidence")

    def test_failed_source_archive_is_preserved_for_forensics(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-archive-preserve-") as value:
            archive = Path(value) / "source.tar"
            with mock.patch.object(
                release_package,
                "_run_git",
                side_effect=release_package.WitnessReleasePreparationError("archive failed"),
            ):
                with self.assertRaisesRegex(
                    release_package.WitnessReleasePreparationError,
                    "archive failed",
                ):
                    release_package._archive_source_tree(ROOT, archive)

            self.assertTrue(archive.exists())
            self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)

    def test_profile_requires_the_pinned_60_second_contract(self):
        profile = release_package._load_profile(release_package.DEFAULT_PROFILE_PATH)
        self.assertEqual(profile["source_commit"], release_package.PINNED_SOURCE_COMMIT)
        self.assertEqual(profile["witness"]["lease_duration_seconds"], 60)
        self.assertTrue(profile["witness"]["enforce_configured_lease_duration"])
        self.assertEqual(profile["webapp_fi_client"]["renew_interval_seconds"], 10)

    def test_webapp_fi_timing_attestation_never_emits_secret_fields(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-fi-timing-") as value:
            config_path = Path(value) / "fi-agent.json"
            config_path.write_text(json.dumps(agent_config()), encoding="utf-8")
            config_path.chmod(0o600)

            attestation = release_package.verify_webapp_fi_client_timing(
                agent_config_path=config_path,
            )

            encoded = json.dumps(attestation, sort_keys=True)
            self.assertTrue(attestation["compatible"])
            self.assertEqual(attestation["timing"]["lease_duration_seconds"], 60)
            self.assertNotIn("secret", encoded.lower())
            self.assertNotIn("key_id", encoded)

    def test_webapp_fi_legacy_180_second_timing_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-fi-legacy-") as value:
            config_path = Path(value) / "fi-agent.json"
            config_path.write_text(json.dumps(agent_config(duration=180)), encoding="utf-8")
            config_path.chmod(0o600)

            with self.assertRaisesRegex(
                release_package.WitnessReleasePreparationError,
                "timing is incompatible",
            ):
                release_package.verify_webapp_fi_client_timing(
                    agent_config_path=config_path,
                )


if __name__ == "__main__":
    unittest.main()
