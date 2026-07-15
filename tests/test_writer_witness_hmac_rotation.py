import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
ROTATION_PATH = ROOT / "deploy/writer-witness/writer-witness-rotate-hmac.py"
SMOKE_PATH = ROOT / "scripts/smoke_writer_witness_client.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


rotation = _load("writer_witness_rotate_hmac_test", ROTATION_PATH)
smoke = _load("smoke_writer_witness_client_test", SMOKE_PATH)


class WriterWitnessHmacRotationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="witness-hmac-rotation-")
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime.env"
        self.client_dir = self.root / "clients"
        self.client_dir.mkdir()
        self.state_root = self.root / "state"
        self.fi_secret = "f" * 64
        self.ir_secret = "i" * 64
        self.runtime.write_text(
            "\n".join(
                (
                    "WRITER_WITNESS_SERVICE_WEBAPP_FI_KEY_ID=webapp-fi-v1",
                    f"WRITER_WITNESS_SERVICE_WEBAPP_FI_SECRET={self.fi_secret}",
                    "WRITER_WITNESS_SERVICE_WEBAPP_IR_KEY_ID=webapp-ir-v1",
                    f"WRITER_WITNESS_SERVICE_WEBAPP_IR_SECRET={self.ir_secret}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.client_dir / "webapp-fi.env").write_text(
            "\n".join(
                (
                    "WRITER_WITNESS_INTERNAL_URL=https://192.0.2.1",
                    "WRITER_WITNESS_CLIENT_KEY_ID=webapp-fi-v1",
                    f"WRITER_WITNESS_CLIENT_SECRET={self.fi_secret}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.client_dir / "webapp-ir.env").write_text(
            "\n".join(
                (
                    "WRITER_WITNESS_INTERNAL_URL=https://192.0.2.1",
                    "WRITER_WITNESS_CLIENT_KEY_ID=webapp-ir-v1",
                    f"WRITER_WITNESS_CLIENT_SECRET={self.ir_secret}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        for path in (self.runtime, *self.client_dir.iterdir()):
            path.chmod(0o600)
        self.guards = (
            patch.object(rotation, "_require_root"),
            patch.object(rotation, "_require_dark_state"),
            patch.object(rotation, "_restart_and_verify"),
            patch.object(rotation.secrets, "token_hex", return_value="n" * 64),
        )
        for guard in self.guards:
            guard.start()

    def tearDown(self):
        for guard in reversed(self.guards):
            guard.stop()
        self.temporary.cleanup()

    def _values(self, path: Path) -> dict[str, str]:
        return rotation._read_env(path)[1]

    def test_prepare_revoke_and_finish_preserve_overlap_then_remove_old_key(self):
        prepared = rotation.prepare(
            "webapp_fi", 0, self.runtime, self.client_dir, self.state_root
        )
        runtime = self._values(self.runtime)
        client = self._values(self.client_dir / "webapp-fi.env")
        self.assertEqual(prepared["old_key_id"], "webapp-fi-v1")
        self.assertEqual(prepared["new_key_id"], "webapp-fi-v2")
        self.assertEqual(runtime["WRITER_WITNESS_SERVICE_WEBAPP_FI_KEY_ID"], "webapp-fi-v2")
        self.assertEqual(runtime["WRITER_WITNESS_SERVICE_WEBAPP_FI_SECRET"], "n" * 64)
        self.assertEqual(
            runtime["WRITER_WITNESS_SERVICE_WEBAPP_FI_PREVIOUS_KEY_ID"],
            "webapp-fi-v1",
        )
        self.assertEqual(
            runtime["WRITER_WITNESS_SERVICE_WEBAPP_FI_PREVIOUS_SECRET"],
            self.fi_secret,
        )
        self.assertEqual(client["WRITER_WITNESS_CLIENT_KEY_ID"], "webapp-fi-v2")
        metadata = (self.state_root / "webapp_fi" / "metadata.json").read_text()
        self.assertNotIn(self.fi_secret, metadata)
        self.assertNotIn("n" * 64, metadata)

        revoked = rotation.revoke(
            "webapp_fi", 0, self.runtime, self.client_dir, self.state_root
        )
        runtime = self._values(self.runtime)
        self.assertEqual(revoked["phase"], "revoked")
        self.assertNotIn("WRITER_WITNESS_SERVICE_WEBAPP_FI_PREVIOUS_KEY_ID", runtime)
        self.assertNotIn("WRITER_WITNESS_SERVICE_WEBAPP_FI_PREVIOUS_SECRET", runtime)
        finished = rotation.finish("webapp_fi", self.state_root)
        self.assertEqual(finished["phase"], "finished")
        self.assertFalse(self.state_root.exists())

    def test_rollback_restores_original_runtime_and_client(self):
        runtime_before = self.runtime.read_bytes()
        client_path = self.client_dir / "webapp-ir.env"
        client_before = client_path.read_bytes()
        rotation.prepare("webapp_ir", 0, self.runtime, self.client_dir, self.state_root)
        rolled_back = rotation.rollback(
            "webapp_ir", 0, self.runtime, self.client_dir, self.state_root
        )
        self.assertEqual(rolled_back["phase"], "rolled_back")
        self.assertEqual(self.runtime.read_bytes(), runtime_before)
        self.assertEqual(client_path.read_bytes(), client_before)
        rotation.finish("webapp_ir", self.state_root)

    def test_prepare_refuses_an_existing_overlap_slot(self):
        with self.runtime.open("a", encoding="utf-8") as handle:
            handle.write("WRITER_WITNESS_SERVICE_WEBAPP_FI_PREVIOUS_KEY_ID=webapp-fi-v0\n")
            handle.write(f"WRITER_WITNESS_SERVICE_WEBAPP_FI_PREVIOUS_SECRET={'p' * 64}\n")
        with self.assertRaisesRegex(rotation.RotationError, "already has an overlap"):
            rotation.prepare("webapp_fi", 0, self.runtime, self.client_dir, self.state_root)


class WriterWitnessSmokeExpectedStatusTests(unittest.TestCase):
    def test_revoked_credential_can_be_asserted_as_401_without_secret_output(self):
        with tempfile.TemporaryDirectory(prefix="witness-smoke-") as directory:
            root = Path(directory)
            env_path = root / "client.env"
            ca_path = root / "ca.crt"
            secret = "s" * 64
            env_path.write_text(
                "\n".join(
                    (
                        "WRITER_WITNESS_INTERNAL_URL=https://192.0.2.1",
                        "WRITER_WITNESS_CLIENT_KEY_ID=webapp-fi-v1",
                        f"WRITER_WITNESS_CLIENT_SECRET={secret}",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            ca_path.write_text("test-ca", encoding="utf-8")
            output = io.StringIO()
            error = HTTPError("https://192.0.2.1", 401, "unauthorized", {}, None)
            argv = [
                str(SMOKE_PATH),
                "--env-file",
                str(env_path),
                "--ca-bundle",
                str(ca_path),
                "--site",
                "webapp_fi",
                "--expect-http-status",
                "401",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(smoke.ssl, "create_default_context", return_value=object()),
                patch.object(smoke, "urlopen", side_effect=error),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(smoke.main(), 0)
            rendered = output.getvalue()
            self.assertIn('"http_status": 401', rendered)
            self.assertNotIn(secret, rendered)


if __name__ == "__main__":
    unittest.main()
