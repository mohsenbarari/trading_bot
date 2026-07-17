import importlib.util
import fcntl
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RENDERER_PATH = ROOT / "scripts/render_writer_witness_credentials.py"


def _load_renderer():
    spec = importlib.util.spec_from_file_location(
        "render_writer_witness_credentials_test",
        RENDERER_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


renderer = _load_renderer()


class WriterWitnessCredentialRendererTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="writer-witness-credentials-")
        self.root = Path(self.temporary.name)
        self.uid = os.geteuid()
        self.gid = os.getegid()
        self.etc = self.root / "etc"
        self.etc.mkdir(mode=0o750)
        self.clients = self.root / "clients"
        self.clients.mkdir(mode=0o700)
        self.state_parent = self.root / "state"
        self.state_parent.mkdir(mode=0o700)
        self.marker = self.state_parent / "credential-state.json"
        self.hmac_state = self.root / "hmac-rotation"
        self.hmac_state.mkdir(mode=0o700)
        self.runtime = self.etc / "runtime.env"
        self.bootstrap = self.etc / "bootstrap-secrets.env"
        self.fi_secret = "f" * 64
        self.ir_secret = "e" * 64
        self.db_migrator = "a" * 64
        self.db_runtime = "b" * 64
        self.bootstrap.write_text(
            "\n".join(
                (
                    f"WITNESS_DB_MIGRATOR_PASSWORD={self.db_migrator}",
                    f"WITNESS_DB_RUNTIME_PASSWORD={self.db_runtime}",
                    "WITNESS_FI_KEY_ID=webapp-fi-v1",
                    f"WITNESS_FI_HMAC_SECRET={self.fi_secret}",
                    "WITNESS_IR_KEY_ID=webapp-ir-v1",
                    f"WITNESS_IR_HMAC_SECRET={self.ir_secret}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        self.bootstrap.chmod(0o600)

    def tearDown(self):
        self.temporary.cleanup()

    def _render(self):
        return renderer.render_credentials(
            runtime_env=self.runtime,
            client_dir=self.clients,
            bootstrap_secrets=self.bootstrap,
            marker=self.marker,
            hmac_state_root=self.hmac_state,
            internal_url="https://192.0.2.10",
            public_key="A" * 43 + "=",
            private_key_file="/etc/trading-bot-witness/writer-witness-ed25519",
            expected_uid=self.uid,
            expected_gid=self.gid,
        )

    def _values(self, path: Path) -> dict[str, str]:
        return renderer._read_env(
            path,
            expected_uid=self.uid,
            expected_gid=self.gid,
        )[1]

    def _replace(self, path: Path, before: str, after: str) -> None:
        payload = path.read_text(encoding="utf-8").replace(before, after)
        path.write_text(payload, encoding="utf-8")
        path.chmod(0o600)

    def test_initial_render_is_durable_and_scrubs_bootstrap_hmac_material(self):
        result = self._render()

        self.assertEqual(result["credential_source"], "initial")
        self.assertEqual(self.marker.stat().st_mode & 0o777, 0o600)
        runtime = self._values(self.runtime)
        fi = self._values(self.clients / "webapp-fi.env")
        ir = self._values(self.clients / "webapp-ir.env")
        self.assertEqual(runtime["WRITER_WITNESS_SERVICE_WEBAPP_FI_SECRET"], self.fi_secret)
        self.assertEqual(runtime["WRITER_WITNESS_SERVICE_WEBAPP_IR_SECRET"], self.ir_secret)
        self.assertEqual(fi["WRITER_WITNESS_CLIENT_SECRET"], self.fi_secret)
        self.assertEqual(ir["WRITER_WITNESS_CLIENT_SECRET"], self.ir_secret)
        bootstrap = self._values(self.bootstrap)
        self.assertEqual(set(bootstrap), renderer.BOOTSTRAP_DATABASE_KEYS)
        self.assertNotIn(self.fi_secret, self.bootstrap.read_text(encoding="utf-8"))
        self.assertNotIn(self.ir_secret, self.bootstrap.read_text(encoding="utf-8"))

    def test_reprovision_preserves_rotated_credentials_and_cannot_revive_old_key(self):
        self._render()
        new_key = "webapp-fi-v2"
        new_secret = "c" * 64
        self._replace(
            self.runtime,
            "WRITER_WITNESS_SERVICE_WEBAPP_FI_KEY_ID=webapp-fi-v1",
            f"WRITER_WITNESS_SERVICE_WEBAPP_FI_KEY_ID={new_key}",
        )
        self._replace(
            self.runtime,
            f"WRITER_WITNESS_SERVICE_WEBAPP_FI_SECRET={self.fi_secret}",
            f"WRITER_WITNESS_SERVICE_WEBAPP_FI_SECRET={new_secret}",
        )
        fi_client = self.clients / "webapp-fi.env"
        self._replace(
            fi_client,
            "WRITER_WITNESS_CLIENT_KEY_ID=webapp-fi-v1",
            f"WRITER_WITNESS_CLIENT_KEY_ID={new_key}",
        )
        self._replace(
            fi_client,
            f"WRITER_WITNESS_CLIENT_SECRET={self.fi_secret}",
            f"WRITER_WITNESS_CLIENT_SECRET={new_secret}",
        )

        result = self._render()

        self.assertEqual(result["credential_source"], "preserved")
        runtime = self._values(self.runtime)
        self.assertEqual(runtime["WRITER_WITNESS_SERVICE_WEBAPP_FI_KEY_ID"], new_key)
        self.assertEqual(runtime["WRITER_WITNESS_SERVICE_WEBAPP_FI_SECRET"], new_secret)
        combined = self.runtime.read_text() + self.bootstrap.read_text()
        self.assertNotIn(self.fi_secret, combined)
        self.assertNotIn("webapp-fi-v1", combined)

    def test_initialized_marker_blocks_reconstruction_when_files_are_missing(self):
        self._render()
        self.runtime.unlink()
        (self.clients / "webapp-fi.env").unlink()
        (self.clients / "webapp-ir.env").unlink()

        with self.assertRaisesRegex(
            renderer.CredentialRenderError,
            "initialized credential files are incomplete",
        ):
            self._render()

    def test_partial_initial_publication_can_resume_only_when_it_matches_bootstrap(self):
        fi_client = self.clients / "webapp-fi.env"
        fi_client.write_text(
            "\n".join(
                (
                    "WRITER_WITNESS_CLIENT_KEY_ID=webapp-fi-v1",
                    f"WRITER_WITNESS_CLIENT_SECRET={self.fi_secret}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        fi_client.chmod(0o600)

        result = self._render()

        self.assertEqual(result["credential_source"], "initial-resume")
        self.assertTrue(self.runtime.is_file())
        self.assertTrue((self.clients / "webapp-ir.env").is_file())

    def test_partial_initial_publication_with_foreign_secret_fails_closed(self):
        fi_client = self.clients / "webapp-fi.env"
        fi_client.write_text(
            "WRITER_WITNESS_CLIENT_KEY_ID=webapp-fi-v1\n"
            f"WRITER_WITNESS_CLIENT_SECRET={'0' * 64}\n",
            encoding="utf-8",
        )
        fi_client.chmod(0o600)

        with self.assertRaisesRegex(renderer.CredentialRenderError, "does not match"):
            self._render()
        self.assertFalse(self.runtime.exists())

    def test_active_rotation_state_blocks_reprovision_without_mutation(self):
        self._render()
        runtime_before = self.runtime.read_bytes()
        (self.hmac_state / "webapp_fi").mkdir(mode=0o700)

        with self.assertRaisesRegex(renderer.CredentialRenderError, "rotation state"):
            self._render()
        self.assertEqual(self.runtime.read_bytes(), runtime_before)

    def test_active_rotation_lock_blocks_reprovision_but_idle_lock_is_permanent(self):
        self._render()
        runtime_before = self.runtime.read_bytes()
        lock_path = self.hmac_state / ".runtime.lock"
        descriptor = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(renderer.CredentialRenderError, "rotation is active"):
                self._render()
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        self.assertEqual(self.runtime.read_bytes(), runtime_before)
        self.assertEqual(self._render()["credential_source"], "preserved")

    def test_inherited_rotation_lock_remains_held_across_renderer_process_boundary(self):
        lock_path = self.hmac_state / ".runtime.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            renderer.render_credentials(
                runtime_env=self.runtime,
                client_dir=self.clients,
                bootstrap_secrets=self.bootstrap,
                marker=self.marker,
                hmac_state_root=self.hmac_state,
                internal_url="https://192.0.2.10",
                public_key="A" * 43 + "=",
                private_key_file="/etc/trading-bot-witness/writer-witness-ed25519",
                expected_uid=self.uid,
                expected_gid=self.gid,
                finalize=False,
                rotation_lock_fd=descriptor,
            )
            competitor = os.open(lock_path, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(competitor)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def test_existing_overlap_credential_blocks_reprovision(self):
        self._render()
        with self.runtime.open("a", encoding="utf-8") as handle:
            handle.write("WRITER_WITNESS_SERVICE_WEBAPP_FI_PREVIOUS_KEY_ID=old-fi\n")
            handle.write(f"WRITER_WITNESS_SERVICE_WEBAPP_FI_PREVIOUS_SECRET={'1' * 64}\n")

        with self.assertRaisesRegex(renderer.CredentialRenderError, "overlap"):
            self._render()

    def test_symlinked_bootstrap_is_rejected_without_touching_target(self):
        target = self.root / "foreign.env"
        target.write_bytes(self.bootstrap.read_bytes())
        target.chmod(0o600)
        self.bootstrap.unlink()
        self.bootstrap.symlink_to(target)

        with self.assertRaisesRegex(renderer.CredentialRenderError, "unsafe credential file"):
            self._render()
        self.assertIn(self.fi_secret, target.read_text(encoding="utf-8"))

    def test_prepare_crash_keeps_bootstrap_recoverable_until_activation_commit(self):
        candidate = self.root / "candidate"
        candidate.mkdir(mode=0o700)
        candidate_clients = candidate / "clients"
        candidate_clients.mkdir(mode=0o700)

        result = renderer.render_credentials(
            runtime_env=candidate / "runtime.env",
            client_dir=candidate_clients,
            current_runtime_env=self.runtime,
            current_client_dir=self.clients,
            bootstrap_secrets=self.bootstrap,
            marker=self.marker,
            hmac_state_root=self.hmac_state,
            internal_url="https://192.0.2.10",
            public_key="A" * 43 + "=",
            private_key_file="/etc/trading-bot-witness/writer-witness-ed25519",
            expected_uid=self.uid,
            expected_gid=self.gid,
            finalize=False,
        )
        self.assertEqual(result["credential_source"], "initial")
        self.assertFalse(self.marker.exists())
        self.assertIn(self.fi_secret, self.bootstrap.read_text(encoding="utf-8"))

        # A crash before activation publication may discard the candidate. The
        # untouched bootstrap remains sufficient for an exact retry.
        for path in candidate_clients.iterdir():
            path.unlink()
        (candidate / "runtime.env").unlink()
        renderer.render_credentials(
            runtime_env=candidate / "runtime.env",
            client_dir=candidate_clients,
            current_runtime_env=self.runtime,
            current_client_dir=self.clients,
            bootstrap_secrets=self.bootstrap,
            marker=self.marker,
            hmac_state_root=self.hmac_state,
            internal_url="https://192.0.2.10",
            public_key="A" * 43 + "=",
            private_key_file="/etc/trading-bot-witness/writer-witness-ed25519",
            expected_uid=self.uid,
            expected_gid=self.gid,
            finalize=False,
        )

        renderer.finalize_credentials(
            current_runtime_env=candidate / "runtime.env",
            current_client_dir=candidate_clients,
            bootstrap_secrets=self.bootstrap,
            marker=self.marker,
            hmac_state_root=self.hmac_state,
            expected_uid=self.uid,
            expected_gid=self.gid,
        )
        self.assertTrue(self.marker.is_file())
        self.assertNotIn(self.fi_secret, self.bootstrap.read_text(encoding="utf-8"))

    def test_bootstrap_initialization_is_exclusive_and_database_export_is_fixed_schema(self):
        bootstrap_dir = self.root / "bootstrap-group-readable"
        bootstrap_dir.mkdir(mode=0o750)
        fresh = bootstrap_dir / "bootstrap.env"
        created = renderer.initialize_bootstrap(
            fresh,
            expected_uid=self.uid,
            expected_gid=self.gid,
        )
        self.assertTrue(created["bootstrap_created"])
        first = fresh.read_bytes()
        repeated = renderer.initialize_bootstrap(
            fresh,
            expected_uid=self.uid,
            expected_gid=self.gid,
        )
        self.assertFalse(repeated["bootstrap_created"])
        self.assertEqual(fresh.read_bytes(), first)

        export_dir = self.root / "database-export"
        export_dir.mkdir(mode=0o700)
        exported = export_dir / "database.env"
        renderer.write_database_env(
            bootstrap_secrets=fresh,
            output=exported,
            expected_uid=self.uid,
            expected_gid=self.gid,
        )
        values = self._values(exported)
        self.assertEqual(set(values), renderer.BOOTSTRAP_DATABASE_KEYS)
        self.assertTrue(all(renderer.SECRET_PATTERN.fullmatch(value) for value in values.values()))


if __name__ == "__main__":
    unittest.main()
