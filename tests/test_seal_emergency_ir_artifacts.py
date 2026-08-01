import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
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


RECIPIENT = "age1hxt7paq6kp3cr4ey6tp0ne2dpvmz7az9h7jh09vfr9gpsm30fa7qa8zmkt"
KINDS = ("image_bundle", "package_tar", "snapshot", "settings")


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


class SealEmergencyIrArtifactsTests(unittest.TestCase):
    def test_seals_four_local_artifacts_and_writes_a_strict_publish_plan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="seal-emergency-ir-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            output = root / "output"
            output.mkdir(mode=0o700)
            age = root / "fake-age"
            fake_age(age)
            plain = {}
            for kind in KINDS:
                path = root / f"{kind}.plain"
                root_file(path, (kind + "-payload").encode("ascii"))
                plain[kind] = path

            with patch.object(SEAL, "DEFAULT_AGE_BINARY", age):
                result = SEAL.seal_artifacts(
                    campaign_id="campaign-20260801",
                    bucket="gold-trade-emergency-ir-20260801",
                    prefix="emergency-ir",
                    created_at="2026-08-01T22:30:00Z",
                    plaintext_paths=plain,
                    output_directory=output,
                )

            self.assertEqual(result["status"], "sealed-local-only")
            self.assertEqual(result["campaign_id"], "campaign-20260801")
            plan_path = output / "publish-plan.json"
            self.assertTrue(plan_path.is_file())
            self.assertEqual(stat.S_IMODE(plan_path.stat().st_mode), 0o600)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual([item["kind"] for item in plan["artifacts"]], list(KINDS))
            self.assertEqual(plan["destination_age_recipient_key_id"], result["destination_age_recipient_key_id"])
            self.assertEqual(result["destination_age_recipient"], RECIPIENT)
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
            plain = {}
            for kind in KINDS:
                path = root / f"{kind}.plain"
                root_file(path, b"payload")
                plain[kind] = path

            with patch.object(SEAL, "DEFAULT_AGE_BINARY", age):
                with self.assertRaisesRegex(SEAL.EmergencyArtifactSealError, "must be empty"):
                    SEAL.seal_artifacts(
                        campaign_id="campaign-20260801",
                        bucket="gold-trade-emergency-ir-20260801",
                        prefix="emergency-ir",
                        created_at="2026-08-01T22:30:00Z",
                        plaintext_paths=plain,
                        output_directory=output,
                    )
            self.assertEqual((output / "prior").read_bytes(), b"preserve")
            self.assertEqual(len(list(output.iterdir())), 1)

    def test_canonical_timestamp_and_recipient_reject_unsafe_values(self) -> None:
        with self.assertRaisesRegex(SEAL.EmergencyArtifactSealError, "created_at"):
            SEAL._canonical_created_at("2026-08-01T22:30:00+00:00")
        with patch.object(SEAL, "WA_IR_AGE_RECIPIENT", "not-an-age-recipient"):
            with self.assertRaisesRegex(SEAL.EmergencyArtifactSealError, "recipient"):
                SEAL._wa_ir_recipient_key_id()

    def test_cli_requires_isolated_interpreter(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("python3 -I -B", completed.stdout)

    def test_finalization_never_replaces_a_target_created_after_precheck(self) -> None:
        with tempfile.TemporaryDirectory(prefix="seal-emergency-ir-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            temporary = root / ".images.tar.age.1.part"
            output = root / "images.tar.age"
            root_file(temporary, b"candidate")

            def race_link(source: Path, destination: Path, **_: object) -> None:
                self.assertEqual(source, temporary)
                self.assertEqual(destination, output)
                root_file(output, b"racing-owner")
                raise FileExistsError

            with patch.object(SEAL.os, "link", side_effect=race_link):
                with self.assertRaisesRegex(SEAL.EmergencyArtifactSealError, "overwrite existing"):
                    SEAL._finalize_create_only(
                        temporary=temporary,
                        output_path=output,
                        label="image_bundle",
                    )
            self.assertEqual(output.read_bytes(), b"racing-owner")
            self.assertEqual(temporary.read_bytes(), b"candidate")

    def test_rejects_symlink_or_writable_pinned_age_binary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="seal-emergency-ir-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            real = root / "real-age"
            fake_age(real)
            link = root / "age-link"
            link.symlink_to(real)
            with patch.object(SEAL, "DEFAULT_AGE_BINARY", link):
                with self.assertRaisesRegex(SEAL.EmergencyArtifactSealError, "root-owned"):
                    SEAL._require_fixed_age_binary()
            real.chmod(0o777)
            with patch.object(SEAL, "DEFAULT_AGE_BINARY", real):
                with self.assertRaisesRegex(SEAL.EmergencyArtifactSealError, "root-owned"):
                    SEAL._require_fixed_age_binary()


if __name__ == "__main__":
    unittest.main()
