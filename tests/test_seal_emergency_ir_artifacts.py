import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "seal_emergency_ir_artifacts.py"
SPEC = importlib.util.spec_from_file_location("seal_emergency_ir_artifacts", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
SEAL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SEAL
SPEC.loader.exec_module(SEAL)

PACKAGE_MODULE_PATH = ROOT / "scripts" / "build_emergency_ir_release_package.py"
PACKAGE_SPEC = importlib.util.spec_from_file_location("build_emergency_ir_release_package", PACKAGE_MODULE_PATH)
assert PACKAGE_SPEC is not None and PACKAGE_SPEC.loader is not None
PACKAGE = importlib.util.module_from_spec(PACKAGE_SPEC)
sys.modules[PACKAGE_SPEC.name] = PACKAGE
PACKAGE_SPEC.loader.exec_module(PACKAGE)


RECIPIENT = "age1hxt7paq6kp3cr4ey6tp0ne2dpvmz7az9h7jh09vfr9gpsm30fa7qa8zmkt"
KINDS = ("image_bundle", "package_tar", "snapshot", "settings")
PATCH_SHA = "a" * 40


def root_file(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.write_bytes(payload)
    path.chmod(mode)


def fake_age(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdout.buffer.write(b'age-encryption.org/v1\\n' + sys.stdin.buffer.read())\n",
        encoding="utf-8",
    )
    path.chmod(0o700)


def valid_package(path: Path, *, patch_sha: str = PATCH_SHA) -> None:
    files = {
        "deploy/emergency-ir/docker-compose.standalone.yml": b"services: {}\n",
        "deploy/emergency-ir/nginx.standalone.conf.template": b"server {}\n",
        "deploy/emergency-ir/reset-emergency-sessions.sql": b"SELECT 1;\n",
        "scripts/render_emergency_ir_standalone_env.py": b"print('render')\n",
        "scripts/verify_emergency_ir_standalone.py": b"print('verify')\n",
        "scripts/verify_emergency_ir_image_provenance.py": b"print('image')\n",
        "scripts/emergency_ir_standalone_activate.py": b"print('activate')\n",
    }
    release = {
        "schema": SEAL.PACKAGE_RELEASE_SCHEMA,
        "source_release_sha": SEAL.SOURCE_RELEASE_SHA,
        "emergency_patch_sha": patch_sha,
        "files": [
            {"path": name, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
            for name, payload in sorted(files.items())
        ],
    }
    with tarfile.open(path, mode="w:gz") as archive:
        entries = {
            f"{SEAL.PACKAGE_ROOT_NAME}/RELEASE.json": (
                json.dumps(release, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
            ),
            **{f"{SEAL.PACKAGE_ROOT_NAME}/{name}": payload for name, payload in files.items()},
        }
        for name, payload in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(payload))
    path.chmod(0o600)


def plaintexts(root: Path) -> dict[str, Path]:
    values: dict[str, Path] = {}
    for kind in KINDS:
        path = root / f"{kind}.plain"
        if kind == "package_tar":
            valid_package(path)
        elif kind == "snapshot":
            root_file(path, b"PGDMPfake-custom-dump")
        else:
            root_file(path, (kind + "-payload").encode("ascii"))
        values[kind] = path
    return values


class SealEmergencyIrArtifactsTests(unittest.TestCase):
    def test_seals_four_local_artifacts_and_writes_a_strict_publish_plan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="seal-emergency-ir-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            output = root / "output"
            output.mkdir(mode=0o700)
            age = root / "fake-age"
            fake_age(age)
            plain = plaintexts(root)

            result = SEAL.seal_artifacts(
                campaign_id="campaign-20260801",
                bucket="gold-trade-emergency-ir-20260801",
                prefix="emergency-ir",
                created_at="2026-08-01T22:30:00Z",
                recipient=RECIPIENT,
                plaintext_paths=plain,
                output_directory=output,
                age_binary=age,
            )

            self.assertEqual(result["status"], "sealed-local-only")
            self.assertEqual(result["campaign_id"], "campaign-20260801")
            plan_path = output / "publish-plan.json"
            self.assertTrue(plan_path.is_file())
            self.assertEqual(stat.S_IMODE(plan_path.stat().st_mode), 0o600)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual([item["kind"] for item in plan["artifacts"]], list(KINDS))
            self.assertEqual(plan["destination_age_recipient_key_id"], result["destination_age_recipient_key_id"])
            self.assertEqual(plan["emergency_patch_sha"], PATCH_SHA)
            self.assertEqual(result["emergency_patch_sha"], PATCH_SHA)
            for item, kind in zip(plan["artifacts"], KINDS, strict=True):
                ciphertext = Path(item["ciphertext_path"])
                self.assertEqual(ciphertext, output / SEAL.OUTPUT_FILENAMES[kind])
                self.assertEqual(stat.S_IMODE(ciphertext.stat().st_mode), 0o600)
                self.assertEqual(ciphertext.read_bytes()[:22], b"age-encryption.org/v1\n")
                self.assertGreater(item["ciphertext_bytes"], item["plaintext_bytes"])
            self.assertFalse(any(path.name.endswith(".part") for path in output.iterdir()))

    def test_refuses_nonempty_output_directory_before_creating_any_ciphertext(self) -> None:
        with tempfile.TemporaryDirectory(prefix="seal-emergency-ir-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            output = root / "output"
            output.mkdir(mode=0o700)
            root_file(output / "prior", b"preserve")
            age = root / "fake-age"
            fake_age(age)
            plain = plaintexts(root)

            with self.assertRaisesRegex(SEAL.EmergencyArtifactSealError, "must be empty"):
                SEAL.seal_artifacts(
                    campaign_id="campaign-20260801",
                    bucket="gold-trade-emergency-ir-20260801",
                    prefix="emergency-ir",
                    created_at="2026-08-01T22:30:00Z",
                    recipient=RECIPIENT,
                    plaintext_paths=plain,
                    output_directory=output,
                    age_binary=age,
                )
            self.assertEqual((output / "prior").read_bytes(), b"preserve")
            self.assertEqual(len(list(output.iterdir())), 1)

    def test_canonical_timestamp_and_recipient_reject_unsafe_values(self) -> None:
        with self.assertRaisesRegex(SEAL.EmergencyArtifactSealError, "created_at"):
            SEAL._canonical_created_at("2026-08-01T22:30:00+00:00")
        with self.assertRaisesRegex(SEAL.EmergencyArtifactSealError, "recipient"):
            SEAL._recipient_key_id("not-an-age-recipient")

    def test_rejects_malformed_package_or_snapshot_before_creating_ciphertext(self) -> None:
        with tempfile.TemporaryDirectory(prefix="seal-emergency-ir-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            output = root / "output"
            output.mkdir(mode=0o700)
            age = root / "fake-age"
            fake_age(age)
            plain = plaintexts(root)
            root_file(plain["snapshot"], b"not-a-custom-dump")
            with self.assertRaisesRegex(SEAL.EmergencyArtifactSealError, "PostgreSQL custom dump"):
                SEAL.seal_artifacts(
                    campaign_id="campaign-20260801",
                    bucket="gold-trade-emergency-ir-20260801",
                    prefix="emergency-ir",
                    created_at="2026-08-01T22:30:00Z",
                    recipient=RECIPIENT,
                    plaintext_paths=plain,
                    output_directory=output,
                    age_binary=age,
                )
            self.assertEqual(list(output.iterdir()), [])

    def test_rejects_readable_database_or_settings_plaintext_before_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="seal-emergency-ir-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            output = root / "output"
            output.mkdir(mode=0o700)
            age = root / "fake-age"
            fake_age(age)
            for kind in ("snapshot", "settings"):
                with self.subTest(kind=kind):
                    plain = plaintexts(root)
                    plain[kind].chmod(0o644)
                    with self.assertRaisesRegex(SEAL.EmergencyArtifactSealError, f"{kind} plaintext"):
                        SEAL.seal_artifacts(
                            campaign_id="campaign-20260801",
                            bucket="gold-trade-emergency-ir-20260801",
                            prefix="emergency-ir",
                            created_at="2026-08-01T22:30:00Z",
                            recipient=RECIPIENT,
                            plaintext_paths=plain,
                            output_directory=output,
                            age_binary=age,
                        )
                    self.assertEqual(list(output.iterdir()), [])

    def test_rejects_duplicate_plaintext_and_unattested_package_before_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="seal-emergency-ir-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            output = root / "output"
            output.mkdir(mode=0o700)
            age = root / "fake-age"
            fake_age(age)
            plain = plaintexts(root)
            plain["settings"] = plain["image_bundle"]
            with self.assertRaisesRegex(SEAL.EmergencyArtifactSealError, "distinct plaintext"):
                SEAL.seal_artifacts(
                    campaign_id="campaign-20260801",
                    bucket="gold-trade-emergency-ir-20260801",
                    prefix="emergency-ir",
                    created_at="2026-08-01T22:30:00Z",
                    recipient=RECIPIENT,
                    plaintext_paths=plain,
                    output_directory=output,
                    age_binary=age,
                )
            self.assertEqual(list(output.iterdir()), [])
            plain = plaintexts(root)
            root_file(plain["package_tar"], b"not-a-gzip-tar")
            with self.assertRaisesRegex(SEAL.EmergencyArtifactSealError, "package tar"):
                SEAL.seal_artifacts(
                    campaign_id="campaign-20260801",
                    bucket="gold-trade-emergency-ir-20260801",
                    prefix="emergency-ir",
                    created_at="2026-08-01T22:30:00Z",
                    recipient=RECIPIENT,
                    plaintext_paths=plain,
                    output_directory=output,
                    age_binary=age,
                )
            self.assertEqual(list(output.iterdir()), [])

    def test_accepts_the_actual_deterministic_release_package_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="seal-emergency-ir-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            output = root / "output"
            output.mkdir(mode=0o700)
            age = root / "fake-age"
            fake_age(age)
            plain = plaintexts(root)
            package_path = root / "actual-package.tar.gz"
            head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
            PACKAGE.build_package(
                repo=ROOT,
                source_release_sha=SEAL.SOURCE_RELEASE_SHA,
                emergency_patch_sha=head,
                output=package_path,
            )
            plain["package_tar"] = package_path
            result = SEAL.seal_artifacts(
                campaign_id="campaign-20260801",
                bucket="gold-trade-emergency-ir-20260801",
                prefix="emergency-ir",
                created_at="2026-08-01T22:30:00Z",
                recipient=RECIPIENT,
                plaintext_paths=plain,
                output_directory=output,
                age_binary=age,
            )
            self.assertEqual(result["emergency_patch_sha"], head)

    def test_ciphertext_finalization_never_replaces_an_existing_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="seal-emergency-ir-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            age = root / "fake-age"
            fake_age(age)
            source = root / "source"
            output = root / "ciphertext.age"
            root_file(source, b"one-local-artifact")
            with patch.object(SEAL.os, "link", side_effect=FileExistsError):
                with self.assertRaisesRegex(SEAL.EmergencyArtifactSealError, "overwrite existing"):
                    SEAL._seal_one(
                        source_path=source,
                        output_path=output,
                        recipient=RECIPIENT,
                        age_binary=age,
                        label="settings",
                    )
            self.assertFalse(output.exists())
            self.assertEqual(len(list(root.glob(".ciphertext.age.*.part"))), 1)

    def test_cli_requires_isolated_interpreter(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("python3 -I -B", completed.stdout)


if __name__ == "__main__":
    unittest.main()
