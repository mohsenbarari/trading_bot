import hashlib
import importlib.util
import io
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
        "schema": "gold-trade-wa-ir-artifact-stage-config-v3",
        "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
        "region": "ir-thr-at1",
        "bucket": "three-site-private",
        "prefix": "campaign-current/artifacts",
        "age_binary": "/usr/bin/age",
        "age_identity_file": bootstrap.WA_IR_BOOTSTRAP_IDENTITY_FILE,
        "workspace": "/srv/trading-bot-three-site-staging-data/workspace",
        "source_site": "webapp_fi",
        "source_signing_public_key_base64": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "webapp_fi_source_attestation_public_key_base64": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",
        "webapp_fi_controller_authorization_public_key_base64": "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI=",
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
        (source / "core").mkdir(mode=0o700)
        (source / "scripts/manage_webapp_ir_artifact_stage.py").write_text(
            "# stage consumer\nVALUE = 'stage'\n", encoding="utf-8"
        )
        (source / "scripts/manage_webapp_ir_snapshot.py").write_text(
            "# snapshot primitives\nVALUE = 'snapshot'\n", encoding="utf-8"
        )
        (source / "scripts/manage_webapp_ir_release_provenance.py").write_text(
            "# release provenance primitives\nVALUE = 'provenance'\n", encoding="utf-8"
        )
        (source / "scripts/verify_webapp_fi_source_provenance.py").write_text(
            "# pure WebApp-FI source provenance verifier\nVALUE = 'source-provenance'\n",
            encoding="utf-8",
        )
        (source / "core/standby_snapshot_capacity.py").write_text(
            "# capacity primitives\nVALUE = 'capacity'\n", encoding="utf-8"
        )
        (source / "scripts/webapp_ir_image_archive_contract.py").write_text(
            "# isolated image archive tag contract\nVALUE = 'image-contract'\n", encoding="utf-8"
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
            archive = destination / bootstrap.PACKAGE_ARCHIVE_NAME
            receipt = json.loads((destination / bootstrap.PREPARATION_RECEIPT_NAME).read_text())
            self.assertEqual(
                hashlib.sha256(archive.read_bytes()).hexdigest(),
                receipt["bootstrap_archive"]["sha256"],
            )
            self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE((destination / bootstrap.PREPARATION_RECEIPT_NAME).stat().st_mode),
                0o600,
            )
            with tarfile.open(archive, "r:") as package:
                self.assertEqual(sorted(package.getnames()), sorted(bootstrap.PACKAGE_FILES))
                stage = package.extractfile("scripts/manage_webapp_ir_artifact_stage.py")
                self.assertIsNotNone(stage)
                self.assertIn(b"stage", stage.read())
                provenance = package.extractfile("scripts/manage_webapp_ir_release_provenance.py")
                self.assertIsNotNone(provenance)
                self.assertIn(b"provenance", provenance.read())
                source_provenance = package.extractfile("scripts/verify_webapp_fi_source_provenance.py")
                self.assertIsNotNone(source_provenance)
                self.assertIn(b"source-provenance", source_provenance.read())
                capacity = package.extractfile("core/standby_snapshot_capacity.py")
                self.assertIsNotNone(capacity)
                self.assertIn(b"capacity", capacity.read())
                contract = package.extractfile("scripts/webapp_ir_image_archive_contract.py")
                self.assertIsNotNone(contract)
                self.assertIn(b"image-contract", contract.read())
                embedded = package.extractfile(bootstrap.PACKAGE_MANIFEST_MEMBER)
                self.assertIsNotNone(embedded)
                embedded_bytes = embedded.read()
            manifest = json.loads(embedded_bytes)
            self.assertEqual(manifest["control"]["commit"], commit)
            self.assertNotIn("archive", manifest)
            self.assertEqual(
                hashlib.sha256(embedded_bytes).hexdigest(),
                receipt["package_manifest"]["sha256"],
            )
            verified = bootstrap.verify_prepared_bootstrap_package(
                package_directory=destination,
                preparation_receipt=destination / bootstrap.PREPARATION_RECEIPT_NAME,
                expected_control_release_sha=commit,
            )
            self.assertEqual(verified["archive_sha256"], receipt["bootstrap_archive"]["sha256"])
            self.assertEqual(verified["package_manifest_sha256"], receipt["package_manifest"]["sha256"])

    def test_prepare_is_byte_deterministic_for_the_same_control_and_config(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-bootstrap-deterministic-") as value:
            root = Path(value)
            source, commit = self._source(root)
            parent = root / "packages"
            parent.mkdir(mode=0o700)
            first = parent / "candidate-a"
            second = parent / "candidate-b"
            bootstrap.prepare_bootstrap_package(
                source_repository=source,
                control_release_sha=commit,
                consumer_config=self._config(root),
                destination=first,
            )
            bootstrap.prepare_bootstrap_package(
                source_repository=source,
                control_release_sha=commit,
                consumer_config=self._config(root),
                destination=second,
            )
            self.assertEqual(
                (first / bootstrap.PACKAGE_ARCHIVE_NAME).read_bytes(),
                (second / bootstrap.PACKAGE_ARCHIVE_NAME).read_bytes(),
            )

    def test_prepare_closes_over_the_real_control_tree_source_verifier(self):
        """The bootstrap package must obtain the real portable verifier from Git."""

        with tempfile.TemporaryDirectory(prefix="wa-ir-bootstrap-real-control-") as value:
            root = Path(value)
            source = root / "control-source"
            subprocess.run(
                ["/usr/bin/git", "clone", "--quiet", "--no-local", str(ROOT), str(source)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            parent = root / "packages"
            parent.mkdir(mode=0o700)
            commit = self._run("rev-parse", "HEAD", cwd=source)
            destination = parent / "candidate"
            bootstrap.prepare_bootstrap_package(
                source_repository=source,
                control_release_sha=commit,
                consumer_config=self._config(root),
                destination=destination,
            )
            with tarfile.open(destination / bootstrap.PACKAGE_ARCHIVE_NAME, "r:") as package:
                verifier = package.extractfile("scripts/verify_webapp_fi_source_provenance.py")
                self.assertIsNotNone(verifier)
                self.assertIn(b"verify_source_role_attestation_payload", verifier.read())

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

    def test_prepare_rejects_consumer_config_with_an_unpinned_identity_path(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-bootstrap-identity-") as value:
            root = Path(value)
            source, commit = self._source(root)
            parent = root / "packages"
            parent.mkdir(mode=0o700)
            invalid = consumer_config()
            invalid["age_identity_file"] = "/etc/trading-bot-three-site/wa-ir/untrusted.agekey"
            with self.assertRaisesRegex(bootstrap.BootstrapPreparationError, "pin the WA-IR bootstrap age identity"):
                bootstrap.prepare_bootstrap_package(
                    source_repository=source,
                    control_release_sha=commit,
                    consumer_config=self._config(root, invalid),
                    destination=parent / "candidate",
                )

    def test_prepare_rejects_consumer_config_with_an_invalid_controller_authorization_key(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-bootstrap-controller-key-") as value:
            root = Path(value)
            source, commit = self._source(root)
            parent = root / "packages"
            parent.mkdir(mode=0o700)
            invalid = consumer_config()
            invalid["webapp_fi_controller_authorization_public_key_base64"] = "AQ=="
            with self.assertRaisesRegex(bootstrap.BootstrapPreparationError, "controller authorization key"):
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
                info.mode = 0o600
                info.uid = 0
                info.gid = 0
                info.mtime = 0
                archive.addfile(info, io.BytesIO(b"x"))
            with self.assertRaisesRegex(bootstrap.BootstrapPreparationError, "does not match expected"):
                bootstrap._verify_archive(path, {"scripts/manage_webapp_ir_artifact_stage.py": "0" * 64})


if __name__ == "__main__":
    unittest.main()
