import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/prepare_webapp_ir_stage_bootstrap.py"
SPEC = importlib.util.spec_from_file_location("prepare_webapp_ir_stage_bootstrap", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def consumer_config() -> dict[str, object]:
    return {
        "schema": "gold-trade-wa-ir-artifact-stage-config-v1",
        "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
        "region": "ir-thr-at1",
        "bucket": "three-site-private",
        "prefix": "campaign-current/artifacts",
        "age_binary": "/usr/bin/age",
        "age_identity_file": "/etc/trading-bot-three-site/wa-ir/artifact-stage.agekey",
        "workspace": "/srv/trading-bot-three-site-staging-data/workspace",
        "source_site": "webapp_fi",
        "source_signing_public_key_base64": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "maximum_artifact_bytes": 21474836480,
    }


class WebAppIrStageBootstrapTests(unittest.TestCase):
    def _run(self, *arguments: str, cwd: Path) -> str:
        result = subprocess.run(
            ["/usr/bin/git", *arguments],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return result.stdout.strip()

    def _source(self, root: Path) -> tuple[Path, str]:
        source = root / "source"
        (source / "scripts").mkdir(parents=True, mode=0o700)
        (source / "scripts/manage_webapp_ir_artifact_stage.py").write_text(
            "# stage consumer\nVALUE = 'stage'\n", encoding="utf-8"
        )
        (source / "scripts/manage_webapp_ir_snapshot.py").write_text(
            "# snapshot primitives\nVALUE = 'snapshot'\n", encoding="utf-8"
        )
        self._run("init", "-q", cwd=source)
        self._run("add", ".", cwd=source)
        self._run("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "control", cwd=source)
        return source, self._run("rev-parse", "HEAD", cwd=source)

    def _config(self, root: Path, value: dict[str, object] | None = None, mode: int = 0o600) -> Path:
        path = root / "consumer.json"
        path.write_text(json.dumps(value or consumer_config()), encoding="utf-8")
        path.chmod(mode)
        return path

    def test_prepare_creates_hash_bound_deterministic_package(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-bootstrap-") as value:
            root = Path(value)
            source, commit = self._source(root)
            destination_parent = root / "packages"
            destination_parent.mkdir(mode=0o700)
            destination = destination_parent / "candidate"
            result = bootstrap.prepare_bootstrap_package(
                source_repository=source,
                control_release_sha=commit,
                consumer_config=self._config(root),
                destination=destination,
            )
            self.assertEqual(result["status"], "prepared")
            self.assertEqual(result["control_commit"], commit)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            archive = destination / "wa-ir-artifact-stage-consumer.tar"
            manifest = json.loads((destination / "bootstrap-package.json").read_text())
            self.assertEqual(manifest["control"]["commit"], commit)
            self.assertEqual(
                hashlib.sha256(archive.read_bytes()).hexdigest(),
                manifest["archive"]["sha256"],
            )
            self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE((destination / "bootstrap-preparation-receipt.json").stat().st_mode),
                0o600,
            )
            with tarfile.open(archive, "r:") as package:
                self.assertEqual(sorted(package.getnames()), sorted(bootstrap.PACKAGE_FILES))
                stage = package.extractfile("scripts/manage_webapp_ir_artifact_stage.py")
                self.assertIsNotNone(stage)
                self.assertIn(b"stage", stage.read())

    def test_prepare_refuses_existing_destination(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-bootstrap-existing-") as value:
            root = Path(value)
            source, commit = self._source(root)
            parent = root / "packages"
            parent.mkdir(mode=0o700)
            destination = parent / "candidate"
            destination.mkdir(mode=0o700)
            with self.assertRaisesRegex(bootstrap.BootstrapPreparationError, "must not already exist"):
                bootstrap.prepare_bootstrap_package(
                    source_repository=source,
                    control_release_sha=commit,
                    consumer_config=self._config(root),
                    destination=destination,
                )

    def test_prepare_rejects_dirty_control_source(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-bootstrap-dirty-") as value:
            root = Path(value)
            source, commit = self._source(root)
            (source / "untracked.txt").write_text("dirty", encoding="utf-8")
            parent = root / "packages"
            parent.mkdir(mode=0o700)
            with self.assertRaisesRegex(bootstrap.BootstrapPreparationError, "must be clean"):
                bootstrap.prepare_bootstrap_package(
                    source_repository=source,
                    control_release_sha=commit,
                    consumer_config=self._config(root),
                    destination=parent / "candidate",
                )

    def test_prepare_rejects_mismatched_head(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-bootstrap-head-") as value:
            root = Path(value)
            source, commit = self._source(root)
            (source / "later.txt").write_text("later", encoding="utf-8")
            self._run("add", ".", cwd=source)
            self._run("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "later", cwd=source)
            parent = root / "packages"
            parent.mkdir(mode=0o700)
            with self.assertRaisesRegex(bootstrap.BootstrapPreparationError, "HEAD does not match"):
                bootstrap.prepare_bootstrap_package(
                    source_repository=source,
                    control_release_sha=commit,
                    consumer_config=self._config(root),
                    destination=parent / "candidate",
                )

    def test_prepare_rejects_nonprivate_destination_parent(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-bootstrap-parent-") as value:
            root = Path(value)
            source, commit = self._source(root)
            parent = root / "packages"
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
            with self.assertRaisesRegex(bootstrap.BootstrapPreparationError, "destination parent must be root-private"):
                bootstrap.prepare_bootstrap_package(
                    source_repository=source,
                    control_release_sha=commit,
                    consumer_config=self._config(root),
                    destination=parent / "candidate",
                )

    def test_prepare_rejects_nonprivate_or_malformed_consumer_config(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-bootstrap-config-") as value:
            root = Path(value)
            source, commit = self._source(root)
            parent = root / "packages"
            parent.mkdir(mode=0o700)
            config = self._config(root, mode=0o644)
            with self.assertRaisesRegex(bootstrap.BootstrapPreparationError, "unsafe ownership"):
                bootstrap.prepare_bootstrap_package(
                    source_repository=source,
                    control_release_sha=commit,
                    consumer_config=config,
                    destination=parent / "candidate-a",
                )
            config.chmod(0o600)
            unsafe = consumer_config()
            unsafe["credentials_file"] = "/root/secret"
            config.write_text(json.dumps(unsafe), encoding="utf-8")
            config.chmod(0o600)
            with self.assertRaisesRegex(bootstrap.BootstrapPreparationError, "fields do not match"):
                bootstrap.prepare_bootstrap_package(
                    source_repository=source,
                    control_release_sha=commit,
                    consumer_config=config,
                    destination=parent / "candidate-b",
                )

    def test_prepare_rejects_consumer_endpoint_that_the_packaged_consumer_would_reject(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-bootstrap-endpoint-") as value:
            root = Path(value)
            source, commit = self._source(root)
            parent = root / "packages"
            parent.mkdir(mode=0o700)
            invalid = consumer_config()
            invalid["endpoint"] = "https://s3.example.invalid"
            with self.assertRaisesRegex(bootstrap.BootstrapPreparationError, "HTTPS Arvan S3 endpoint"):
                bootstrap.prepare_bootstrap_package(
                    source_repository=source,
                    control_release_sha=commit,
                    consumer_config=self._config(root, invalid),
                    destination=parent / "candidate",
                )

    def test_prepare_rejects_group_writable_source(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-bootstrap-source-mode-") as value:
            root = Path(value)
            source, commit = self._source(root)
            source.chmod(0o775)
            parent = root / "packages"
            parent.mkdir(mode=0o700)
            with self.assertRaisesRegex(bootstrap.BootstrapPreparationError, "root-owned and not group/other writable"):
                bootstrap.prepare_bootstrap_package(
                    source_repository=source,
                    control_release_sha=commit,
                    consumer_config=self._config(root),
                    destination=parent / "candidate",
                )

    def test_archive_verifier_rejects_unexpected_entry(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-bootstrap-archive-") as value:
            path = Path(value) / "unsafe.tar"
            with tarfile.open(path, "w:") as archive:
                info = tarfile.TarInfo("unexpected.txt")
                info.size = 1
                archive.addfile(info, __import__("io").BytesIO(b"x"))
            with self.assertRaisesRegex(bootstrap.BootstrapPreparationError, "contents do not match"):
                bootstrap._verify_archive(path, {"scripts/manage_webapp_ir_artifact_stage.py": "0" * 64})


if __name__ == "__main__":
    unittest.main()
