import contextlib
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import os
from pathlib import Path
import signal
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


class SimulatedHardKill(BaseException):
    """Bypass ordinary exception cleanup like SIGKILL would."""


class WriterWitnessHmacRotationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="witness-hmac-rotation-")
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime.env"
        self.client_dir = self.root / "clients"
        self.client_dir.mkdir()
        self.client_dir.chmod(0o700)
        self.state_root = self.root / "state"
        self.campaign_not_after = (
            datetime.now(timezone.utc) + timedelta(minutes=10)
        ).isoformat().replace("+00:00", "Z")
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
            patch.object(rotation, "_require_service_stopped"),
            patch.object(rotation, "_restart_and_verify"),
            patch.object(
                rotation.secrets,
                "token_hex",
                side_effect=lambda byte_count: (
                    "a" * 32 if byte_count == 16 else "n" * (byte_count * 2)
                ),
            ),
        )
        for guard in self.guards:
            guard.start()
        self._original_prepare = rotation.prepare

        def campaign_aware_prepare(*args, **kwargs):
            if kwargs.get("campaign_tag") is not None:
                kwargs.setdefault("campaign_not_after", self.campaign_not_after)
            return self._original_prepare(*args, **kwargs)

        rotation.prepare = campaign_aware_prepare

    def tearDown(self):
        rotation.prepare = self._original_prepare
        for guard in reversed(self.guards):
            guard.stop()
        self.temporary.cleanup()

    def _values(self, path: Path) -> dict[str, str]:
        return rotation._read_env(path)[1]

    def test_prepare_rejects_symlinked_state_root_without_chmodding_target(self):
        outside = self.root / "outside-state"
        outside.mkdir(mode=0o755)
        self.state_root.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(rotation.RotationError, "not safely owned"):
            rotation.prepare(
                "webapp_fi",
                0,
                self.runtime,
                self.client_dir,
                self.state_root,
                campaign_tag="wwm_0123456789ab",
            )
        self.assertEqual(outside.stat().st_mode & 0o777, 0o755)
        self.assertEqual(list(outside.iterdir()), [])

    def test_prepare_rejects_symlinked_or_hardlinked_credential_files(self):
        runtime_payload = self.runtime.read_bytes()
        unrelated = self.root / "unrelated.env"
        unrelated.write_bytes(runtime_payload)
        unrelated.chmod(0o600)
        self.runtime.unlink()
        self.runtime.symlink_to(unrelated)
        with self.assertRaisesRegex(rotation.RotationError, "regular inode"):
            rotation.prepare(
                "webapp_fi",
                0,
                self.runtime,
                self.client_dir,
                self.state_root,
                campaign_tag="wwm_0123456789ab",
            )
        self.assertEqual(unrelated.read_bytes(), runtime_payload)

        self.runtime.unlink()
        self.runtime.write_bytes(runtime_payload)
        self.runtime.chmod(0o600)
        client_path = self.client_dir / "webapp-fi.env"
        client_payload = client_path.read_bytes()
        linked = self.root / "linked-client.env"
        os.link(client_path, linked)
        with self.assertRaisesRegex(rotation.RotationError, "regular inode"):
            rotation.prepare(
                "webapp_fi",
                0,
                self.runtime,
                self.client_dir,
                self.state_root,
                campaign_tag="wwm_0123456789ab",
            )
        self.assertEqual(client_path.read_bytes(), client_payload)

    def _assert_child_was_sigkilled(self, callback) -> None:
        process_id = os.fork()
        if process_id == 0:
            try:
                callback()
            except BaseException:
                os._exit(91)
            os._exit(92)
        _, wait_status = os.waitpid(process_id, 0)
        self.assertTrue(os.WIFSIGNALED(wait_status), wait_status)
        self.assertEqual(os.WTERMSIG(wait_status), signal.SIGKILL)

    def _recover_dual_site_in_order(self, recovery_order: tuple[str, str]) -> None:
        campaign_tag = "wwm_0123456789ab"
        runtime_before = self.runtime.read_bytes()
        client_paths = {
            "webapp_fi": self.client_dir / "webapp-fi.env",
            "webapp_ir": self.client_dir / "webapp-ir.env",
        }
        clients_before = {
            site: path.read_bytes() for site, path in client_paths.items()
        }
        baseline_ids = {
            "webapp_fi": "webapp-fi-v1",
            "webapp_ir": "webapp-ir-v1",
        }
        scenario_ids = {
            "webapp_fi": "matrix-wwm_0123456789ab-fi",
            "webapp_ir": "matrix-wwm_0123456789ab-ir",
        }
        site_suffixes = {"webapp_fi": "FI", "webapp_ir": "IR"}

        # Treat both successful mutations as lost/ambiguous RPC responses. Recovery
        # must use durable state and must not depend on either response being seen.
        for site in ("webapp_fi", "webapp_ir"):
            rotation.prepare(
                site,
                0,
                self.runtime,
                self.client_dir,
                self.state_root,
                campaign_tag=campaign_tag,
            )
            metadata_path = self.state_root / site / "metadata.json"
            metadata = rotation._load_metadata(metadata_path)
            metadata["phase"] = "preparing"
            rotation._write_metadata(metadata_path, metadata)

        runtime = self._values(self.runtime)
        for site, suffix in site_suffixes.items():
            prefix = f"WRITER_WITNESS_SERVICE_WEBAPP_{suffix}"
            self.assertEqual(runtime[f"{prefix}_KEY_ID"], scenario_ids[site])
            self.assertEqual(runtime[f"{prefix}_PREVIOUS_KEY_ID"], baseline_ids[site])
            self.assertEqual(
                self._values(client_paths[site])["WRITER_WITNESS_CLIENT_KEY_ID"],
                scenario_ids[site],
            )

        fi_snapshot = self._values(
            self.state_root / "webapp_fi" / "runtime-site.env.before"
        )
        ir_snapshot = self._values(
            self.state_root / "webapp_ir" / "runtime-site.env.before"
        )
        self.assertEqual(
            set(fi_snapshot),
            {
                "WRITER_WITNESS_SERVICE_WEBAPP_FI_KEY_ID",
                "WRITER_WITNESS_SERVICE_WEBAPP_FI_SECRET",
            },
        )
        self.assertEqual(
            set(ir_snapshot),
            {
                "WRITER_WITNESS_SERVICE_WEBAPP_IR_KEY_ID",
                "WRITER_WITNESS_SERVICE_WEBAPP_IR_SECRET",
            },
        )

        first, second = recovery_order
        rotation.recover(
            first,
            0,
            campaign_tag,
            self.runtime,
            self.client_dir,
            self.state_root,
        )
        runtime = self._values(self.runtime)
        first_prefix = (
            f"WRITER_WITNESS_SERVICE_WEBAPP_{site_suffixes[first]}"
        )
        second_prefix = (
            f"WRITER_WITNESS_SERVICE_WEBAPP_{site_suffixes[second]}"
        )
        self.assertEqual(runtime[f"{first_prefix}_KEY_ID"], baseline_ids[first])
        self.assertNotIn(f"{first_prefix}_PREVIOUS_KEY_ID", runtime)
        self.assertNotIn(f"{first_prefix}_PREVIOUS_SECRET", runtime)
        self.assertEqual(runtime[f"{second_prefix}_KEY_ID"], scenario_ids[second])
        self.assertEqual(
            runtime[f"{second_prefix}_PREVIOUS_KEY_ID"], baseline_ids[second]
        )
        self.assertEqual(client_paths[first].read_bytes(), clients_before[first])
        self.assertEqual(
            self._values(client_paths[second])["WRITER_WITNESS_CLIENT_KEY_ID"],
            scenario_ids[second],
        )

        rotation.recover(
            second,
            0,
            campaign_tag,
            self.runtime,
            self.client_dir,
            self.state_root,
        )
        self.assertEqual(self.runtime.read_bytes(), runtime_before)
        for site, client_path in client_paths.items():
            self.assertEqual(client_path.read_bytes(), clients_before[site])
            self.assertFalse((self.state_root / site).exists())
        runtime = self._values(self.runtime)
        self.assertFalse(any("_PREVIOUS_" in key for key in runtime))
        self.assertNotIn("matrix-", self.runtime.read_text(encoding="utf-8"))

    def test_dual_site_ambiguous_recovery_fi_then_ir_restores_exact_baseline(self):
        self._recover_dual_site_in_order(("webapp_fi", "webapp_ir"))

    def test_dual_site_ambiguous_recovery_ir_then_fi_restores_exact_baseline(self):
        self._recover_dual_site_in_order(("webapp_ir", "webapp_fi"))

    def test_failed_second_site_prepare_preserves_first_site_rotation(self):
        campaign_tag = "wwm_0123456789ab"
        runtime_before = self.runtime.read_bytes()
        ir_client_before = (self.client_dir / "webapp-ir.env").read_bytes()
        fi_client_before = (self.client_dir / "webapp-fi.env").read_bytes()
        rotation.prepare(
            "webapp_fi",
            0,
            self.runtime,
            self.client_dir,
            self.state_root,
            campaign_tag=campaign_tag,
        )
        runtime_after_fi = self.runtime.read_bytes()
        fi_client_after_prepare = (self.client_dir / "webapp-fi.env").read_bytes()

        with patch.object(
            rotation,
            "_restart_and_verify",
            side_effect=(rotation.RotationError("injected restart failure"), None),
        ):
            with self.assertRaisesRegex(rotation.RotationError, "injected restart failure"):
                rotation.prepare(
                    "webapp_ir",
                    0,
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                    campaign_tag=campaign_tag,
                )

        self.assertEqual(self.runtime.read_bytes(), runtime_after_fi)
        self.assertEqual(
            (self.client_dir / "webapp-fi.env").read_bytes(),
            fi_client_after_prepare,
        )
        self.assertEqual((self.client_dir / "webapp-ir.env").read_bytes(), ir_client_before)
        self.assertFalse((self.state_root / "webapp_ir").exists())

        rotation.recover(
            "webapp_fi",
            0,
            campaign_tag,
            self.runtime,
            self.client_dir,
            self.state_root,
        )
        self.assertEqual(self.runtime.read_bytes(), runtime_before)
        self.assertEqual((self.client_dir / "webapp-fi.env").read_bytes(), fi_client_before)

    def test_prepare_is_recoverable_at_every_durable_publication_kill_point(self):
        campaign_tag = "wwm_0123456789ab"
        runtime_before = self.runtime.read_bytes()
        clients_before = {
            path.name: path.read_bytes() for path in self.client_dir.iterdir()
        }
        original_write_metadata = rotation._write_metadata
        original_snapshot = rotation._snapshot_runtime_scope
        original_copy = rotation._copy_secret
        original_publish = rotation._rename_directory_noreplace
        original_update = rotation._atomic_update_env

        stages = (
            "before_staging_metadata",
            "after_staging_metadata",
            "after_runtime_snapshot",
            "after_client_snapshot",
            "after_atomic_publication",
            "after_runtime_update",
            "after_client_update",
            "after_service_restart",
            "before_prepared_metadata",
            "after_prepared_metadata",
        )
        for stage in stages:
            with self.subTest(stage=stage):
                metadata_calls = 0
                update_calls = 0

                def injected_metadata(path, metadata):
                    nonlocal metadata_calls
                    metadata_calls += 1
                    if stage == "before_staging_metadata" and metadata_calls == 1:
                        raise SimulatedHardKill(stage)
                    if stage == "after_staging_metadata" and metadata_calls == 1:
                        original_write_metadata(path, metadata)
                        raise SimulatedHardKill(stage)
                    if stage == "before_prepared_metadata" and metadata_calls == 3:
                        raise SimulatedHardKill(stage)
                    original_write_metadata(path, metadata)
                    if stage == "after_prepared_metadata" and metadata_calls == 3:
                        raise SimulatedHardKill(stage)

                def injected_snapshot(*args, **kwargs):
                    original_snapshot(*args, **kwargs)
                    if stage == "after_runtime_snapshot":
                        raise SimulatedHardKill(stage)

                def injected_copy(*args, **kwargs):
                    original_copy(*args, **kwargs)
                    destination = args[1]
                    if stage == "after_client_snapshot" and destination.name == "client.env.before":
                        raise SimulatedHardKill(stage)

                def injected_publish(*args, **kwargs):
                    original_publish(*args, **kwargs)
                    if stage == "after_atomic_publication":
                        raise SimulatedHardKill(stage)

                def injected_update(*args, **kwargs):
                    nonlocal update_calls
                    update_calls += 1
                    original_update(*args, **kwargs)
                    if stage == "after_runtime_update" and update_calls == 1:
                        raise SimulatedHardKill(stage)
                    if stage == "after_client_update" and update_calls == 2:
                        raise SimulatedHardKill(stage)

                def injected_restart():
                    if stage == "after_service_restart":
                        raise SimulatedHardKill(stage)

                with (
                    patch.object(rotation, "_write_metadata", side_effect=injected_metadata),
                    patch.object(rotation, "_snapshot_runtime_scope", side_effect=injected_snapshot),
                    patch.object(rotation, "_copy_secret", side_effect=injected_copy),
                    patch.object(
                        rotation,
                        "_rename_directory_noreplace",
                        side_effect=injected_publish,
                    ),
                    patch.object(rotation, "_atomic_update_env", side_effect=injected_update),
                    patch.object(rotation, "_restart_and_verify", side_effect=injected_restart),
                ):
                    with self.assertRaises(SimulatedHardKill):
                        rotation.prepare(
                            "webapp_fi",
                            0,
                            self.runtime,
                            self.client_dir,
                            self.state_root,
                            campaign_tag=campaign_tag,
                        )

                recovered = rotation.recover(
                    "webapp_fi",
                    0,
                    campaign_tag,
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                )
                self.assertIn(
                    recovered["phase"],
                    {"already_clean", "recovered"},
                )
                self.assertEqual(self.runtime.read_bytes(), runtime_before)
                for path in self.client_dir.iterdir():
                    self.assertEqual(path.read_bytes(), clients_before[path.name])
                self.assertFalse((self.state_root / "webapp_fi").exists())
                self.assertFalse(
                    any("_PREVIOUS_" in key for key in self._values(self.runtime))
                )

                # A metadata-less hidden directory from the earliest possible
                # kill must not block a new prepare/recover cycle.
                rotation.prepare(
                    "webapp_fi",
                    0,
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                    campaign_tag=campaign_tag,
                )
                rotation.recover(
                    "webapp_fi",
                    0,
                    campaign_tag,
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                )
                self.assertEqual(self.runtime.read_bytes(), runtime_before)

        self.assertEqual(tuple(self.state_root.glob(".webapp_fi.prepare-*")), ())

    def test_real_sigkill_inside_first_metadata_temp_is_owned_and_recoverable(self):
        campaign_tag = "wwm_0123456789ab"
        runtime_before = self.runtime.read_bytes()

        def killed_prepare():
            original_replace = rotation.os.replace

            def kill_before_first_metadata_publish(source, destination):
                source_path = Path(source)
                destination_path = Path(destination)
                if (
                    source_path.name.startswith(".metadata.json.")
                    and destination_path.name == "metadata.json"
                    and ".webapp_fi.prepare-" in destination_path.parent.name
                ):
                    os.kill(os.getpid(), signal.SIGKILL)
                return original_replace(source, destination)

            with patch.object(rotation.os, "replace", side_effect=kill_before_first_metadata_publish):
                rotation.prepare(
                    "webapp_fi",
                    0,
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                    campaign_tag=campaign_tag,
                )

        self._assert_child_was_sigkilled(killed_prepare)
        staging = tuple(self.state_root.glob(".webapp_fi.prepare-*"))
        self.assertEqual(len(staging), 1)
        self.assertFalse((staging[0] / "metadata.json").exists())
        self.assertTrue(any(staging[0].glob(".metadata.json.*")))

        recovered = rotation.recover(
            "webapp_fi",
            0,
            campaign_tag,
            self.runtime,
            self.client_dir,
            self.state_root,
        )
        self.assertEqual(recovered["phase"], "already_clean")
        self.assertEqual(recovered["cleaned_staging"], 1)
        self.assertEqual(self.runtime.read_bytes(), runtime_before)
        self.assertEqual(tuple(self.state_root.glob(".webapp_fi.prepare-*")), ())
        self.assertEqual(tuple(self.state_root.glob(".rotation-delete-*")), ())

    def test_real_sigkill_inside_staging_snapshot_primitives_is_recoverable(self):
        campaign_tag = "wwm_0123456789ab"
        runtime_before = self.runtime.read_bytes()
        clients_before = {
            path.name: path.read_bytes() for path in self.client_dir.iterdir()
        }
        for primitive_prefix in (
            ".runtime-site.env.before.write-",
            ".client.env.before.copy-",
        ):
            with self.subTest(primitive=primitive_prefix):
                def killed_prepare():
                    original_replace = rotation.os.replace

                    def kill_inside_staging_snapshot(source, destination):
                        if Path(source).name.startswith(primitive_prefix):
                            os.kill(os.getpid(), signal.SIGKILL)
                        return original_replace(source, destination)

                    with patch.object(
                        rotation.os,
                        "replace",
                        side_effect=kill_inside_staging_snapshot,
                    ):
                        rotation.prepare(
                            "webapp_fi",
                            0,
                            self.runtime,
                            self.client_dir,
                            self.state_root,
                            campaign_tag=campaign_tag,
                        )

                self._assert_child_was_sigkilled(killed_prepare)
                recovered = rotation.recover(
                    "webapp_fi",
                    0,
                    campaign_tag,
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                )
                self.assertEqual(recovered["phase"], "already_clean")
                self.assertEqual(self.runtime.read_bytes(), runtime_before)
                for path in self.client_dir.iterdir():
                    self.assertEqual(path.read_bytes(), clients_before[path.name])
                self.assertFalse(any(self.state_root.glob(".webapp_fi.prepare-*")))

    def test_real_sigkill_inside_runtime_and_client_atomic_updates_is_recoverable(self):
        campaign_tag = "wwm_0123456789ab"
        runtime_before = self.runtime.read_bytes()
        clients_before = {
            path.name: path.read_bytes() for path in self.client_dir.iterdir()
        }
        operation_token = "a" * 32

        for target_name in ("runtime.env", "webapp-fi.env"):
            with self.subTest(target=target_name):
                def killed_prepare():
                    original_replace = rotation.os.replace

                    def kill_inside_atomic_update(source, destination):
                        source_path = Path(source)
                        if source_path.name.startswith(
                            f".{target_name}.rotate-{operation_token}-"
                        ):
                            os.kill(os.getpid(), signal.SIGKILL)
                        return original_replace(source, destination)

                    with patch.object(rotation.os, "replace", side_effect=kill_inside_atomic_update):
                        rotation.prepare(
                            "webapp_fi",
                            0,
                            self.runtime,
                            self.client_dir,
                            self.state_root,
                            campaign_tag=campaign_tag,
                        )

                self._assert_child_was_sigkilled(killed_prepare)
                recovered = rotation.recover(
                    "webapp_fi",
                    0,
                    campaign_tag,
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                    restart_service=False,
                )
                self.assertEqual(recovered["phase"], "recovered")
                self.assertFalse(recovered["service_restarted"])
                self.assertEqual(self.runtime.read_bytes(), runtime_before)
                for path in self.client_dir.iterdir():
                    self.assertEqual(path.read_bytes(), clients_before[path.name])
                orphan_prefix = f".{target_name}.rotate-{operation_token}-"
                parent = self.runtime.parent if target_name == "runtime.env" else self.client_dir
                self.assertFalse(
                    any(path.name.startswith(orphan_prefix) for path in parent.iterdir())
                )

    def test_real_sigkill_inside_restore_primitives_is_idempotently_recoverable(self):
        campaign_tag = "wwm_0123456789ab"
        runtime_before = self.runtime.read_bytes()
        clients_before = {
            path.name: path.read_bytes() for path in self.client_dir.iterdir()
        }
        operation_token = "a" * 32

        for primitive_prefix in (
            f".runtime.env.write-{operation_token}-",
            f".webapp-fi.env.copy-{operation_token}-",
        ):
            with self.subTest(primitive=primitive_prefix):
                rotation.prepare(
                    "webapp_fi",
                    0,
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                    campaign_tag=campaign_tag,
                )

                def killed_recover():
                    original_replace = rotation.os.replace

                    def kill_inside_restore(source, destination):
                        if Path(source).name.startswith(primitive_prefix):
                            os.kill(os.getpid(), signal.SIGKILL)
                        return original_replace(source, destination)

                    with patch.object(rotation.os, "replace", side_effect=kill_inside_restore):
                        rotation.recover(
                            "webapp_fi",
                            0,
                            campaign_tag,
                            self.runtime,
                            self.client_dir,
                            self.state_root,
                            restart_service=False,
                        )

                self._assert_child_was_sigkilled(killed_recover)
                recovered = rotation.recover(
                    "webapp_fi",
                    0,
                    campaign_tag,
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                    restart_service=False,
                )
                self.assertEqual(recovered["phase"], "recovered")
                self.assertEqual(self.runtime.read_bytes(), runtime_before)
                for path in self.client_dir.iterdir():
                    self.assertEqual(path.read_bytes(), clients_before[path.name])
                self.assertFalse(
                    any(
                        path.name.startswith(primitive_prefix)
                        for parent in (self.runtime.parent, self.client_dir)
                        for path in parent.iterdir()
                    )
                )

    def test_real_sigkill_during_operation_temp_tombstoning_is_recoverable(self):
        campaign_tag = "wwm_0123456789ab"
        operation_token = "a" * 32
        runtime_before = self.runtime.read_bytes()
        rotation.prepare(
            "webapp_fi",
            0,
            self.runtime,
            self.client_dir,
            self.state_root,
            campaign_tag=campaign_tag,
        )
        orphan = self.runtime.parent / (
            f".runtime.env.rotate-{operation_token}-ABCDEF12"
        )
        orphan.write_bytes(b"partial-owned-temp")
        orphan.chmod(0o600)

        def killed_recover():
            original_rename = rotation._rename_directory_noreplace

            def kill_after_temp_tombstone(source, destination):
                result = original_rename(source, destination)
                if Path(destination).name.endswith(".owned-tombstone"):
                    os.kill(os.getpid(), signal.SIGKILL)
                return result

            with patch.object(
                rotation,
                "_rename_directory_noreplace",
                side_effect=kill_after_temp_tombstone,
            ):
                rotation.recover(
                    "webapp_fi",
                    0,
                    campaign_tag,
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                    restart_service=False,
                )

        self._assert_child_was_sigkilled(killed_recover)
        self.assertFalse(orphan.exists())
        self.assertTrue(
            any(
                path.name.endswith(".owned-tombstone")
                for path in self.runtime.parent.iterdir()
            )
        )
        recovered = rotation.recover(
            "webapp_fi",
            0,
            campaign_tag,
            self.runtime,
            self.client_dir,
            self.state_root,
            restart_service=False,
        )
        self.assertEqual(recovered["phase"], "recovered")
        self.assertEqual(self.runtime.read_bytes(), runtime_before)
        self.assertFalse(
            any(
                path.name.endswith(".owned-tombstone")
                for path in self.runtime.parent.iterdir()
            )
        )

    def test_real_sigkill_mid_tombstone_deletion_resumes_idempotently(self):
        campaign_tag = "wwm_0123456789ab"
        runtime_before = self.runtime.read_bytes()
        rotation.prepare(
            "webapp_fi",
            0,
            self.runtime,
            self.client_dir,
            self.state_root,
            campaign_tag=campaign_tag,
        )

        def killed_recover():
            original_unlink = rotation.Path.unlink
            killed = False

            def kill_after_first_owned_unlink(path, *args, **kwargs):
                nonlocal killed
                result = original_unlink(path, *args, **kwargs)
                if (
                    not killed
                    and path.parent.name.startswith(".rotation-delete-webapp_fi-")
                    and path.name != "metadata.json"
                ):
                    killed = True
                    os.kill(os.getpid(), signal.SIGKILL)
                return result

            with patch.object(rotation.Path, "unlink", new=kill_after_first_owned_unlink):
                rotation.recover(
                    "webapp_fi",
                    0,
                    campaign_tag,
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                    restart_service=False,
                )

        self._assert_child_was_sigkilled(killed_recover)
        self.assertTrue(any(self.state_root.glob(".rotation-delete-webapp_fi-*")))
        recovered = rotation.recover(
            "webapp_fi",
            0,
            campaign_tag,
            self.runtime,
            self.client_dir,
            self.state_root,
            restart_service=False,
        )
        self.assertEqual(recovered["phase"], "already_clean")
        self.assertGreaterEqual(recovered["resumed_deletions"], 1)
        self.assertEqual(self.runtime.read_bytes(), runtime_before)
        self.assertFalse(any(self.state_root.glob(".rotation-delete-webapp_fi-*")))

    def test_real_sigkill_during_tombstone_claim_handoff_resumes_idempotently(self):
        campaign_tag = "wwm_0123456789ab"
        runtime_before = self.runtime.read_bytes()
        rotation.prepare(
            "webapp_ir",
            0,
            self.runtime,
            self.client_dir,
            self.state_root,
            campaign_tag=campaign_tag,
        )

        def killed_recover():
            original_rename = rotation._rename_directory_noreplace

            def kill_after_claim_handoff(source, destination):
                result = original_rename(source, destination)
                if Path(destination).name.endswith(".claim"):
                    os.kill(os.getpid(), signal.SIGKILL)
                return result

            with patch.object(
                rotation,
                "_rename_directory_noreplace",
                side_effect=kill_after_claim_handoff,
            ):
                rotation.recover(
                    "webapp_ir",
                    0,
                    campaign_tag,
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                    restart_service=False,
                )

        self._assert_child_was_sigkilled(killed_recover)
        self.assertTrue(any(self.state_root.glob(".rotation-delete-webapp_ir-*.claim")))
        recovered = rotation.recover(
            "webapp_ir",
            0,
            campaign_tag,
            self.runtime,
            self.client_dir,
            self.state_root,
            restart_service=False,
        )
        self.assertEqual(recovered["phase"], "already_clean")
        self.assertEqual(self.runtime.read_bytes(), runtime_before)
        self.assertFalse(any(self.state_root.glob(".rotation-delete-webapp_ir-*")))

    def test_recover_can_restore_credentials_without_restarting_service(self):
        campaign_tag = "wwm_0123456789ab"
        runtime_before = self.runtime.read_bytes()
        rotation.prepare(
            "webapp_ir",
            0,
            self.runtime,
            self.client_dir,
            self.state_root,
            campaign_tag=campaign_tag,
        )
        with patch.object(
            rotation,
            "_restart_and_verify",
            side_effect=AssertionError("restart must stay suppressed"),
        ):
            recovered = rotation.recover(
                "webapp_ir",
                0,
                campaign_tag,
                self.runtime,
                self.client_dir,
                self.state_root,
                restart_service=False,
            )
        self.assertFalse(recovered["service_restarted"])
        self.assertEqual(self.runtime.read_bytes(), runtime_before)

    def test_operation_temp_cleanup_never_removes_foreign_hidden_files(self):
        campaign_tag = "wwm_0123456789ab"
        foreign_token = "b" * 32
        foreign = self.runtime.parent / f".runtime.env.rotate-{foreign_token}-ABCDEF12"
        foreign.write_bytes(b"foreign-hidden-state")
        foreign.chmod(0o600)

        rotation.prepare(
            "webapp_fi",
            0,
            self.runtime,
            self.client_dir,
            self.state_root,
            campaign_tag=campaign_tag,
        )
        rotation.recover(
            "webapp_fi",
            0,
            campaign_tag,
            self.runtime,
            self.client_dir,
            self.state_root,
            restart_service=False,
        )
        self.assertEqual(foreign.read_bytes(), b"foreign-hidden-state")

    def test_malformed_same_token_temp_fails_closed_without_deletion(self):
        campaign_tag = "wwm_0123456789ab"
        rotation.prepare(
            "webapp_fi",
            0,
            self.runtime,
            self.client_dir,
            self.state_root,
            campaign_tag=campaign_tag,
        )
        malformed = self.runtime.parent / (
            f".runtime.env.rotate-{'a' * 32}-not-allowlisted!"
        )
        malformed.write_bytes(b"foreign")
        malformed.chmod(0o600)

        with self.assertRaisesRegex(rotation.RotationError, "strict allowlist"):
            rotation.recover(
                "webapp_fi",
                0,
                campaign_tag,
                self.runtime,
                self.client_dir,
                self.state_root,
                restart_service=False,
            )
        self.assertEqual(malformed.read_bytes(), b"foreign")
        self.assertTrue((self.state_root / "webapp_fi").is_dir())

    def test_leave_service_stopped_requires_service_to_be_stopped_first(self):
        campaign_tag = "wwm_0123456789ab"
        rotation.prepare(
            "webapp_ir",
            0,
            self.runtime,
            self.client_dir,
            self.state_root,
            campaign_tag=campaign_tag,
        )
        runtime_before_attempt = self.runtime.read_bytes()
        with patch.object(
            rotation,
            "_require_service_stopped",
            side_effect=rotation.RotationError("service still active"),
        ):
            with self.assertRaisesRegex(rotation.RotationError, "service still active"):
                rotation.recover(
                    "webapp_ir",
                    0,
                    campaign_tag,
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                    restart_service=False,
                )
        self.assertEqual(self.runtime.read_bytes(), runtime_before_attempt)
        rotation.recover(
            "webapp_ir",
            0,
            campaign_tag,
            self.runtime,
            self.client_dir,
            self.state_root,
        )

    def test_cli_leave_service_stopped_maps_to_no_restart_recovery(self):
        argv = [
            str(ROTATION_PATH),
            "recover",
            "--site",
            "webapp_fi",
            "--expected-epoch",
            "0",
            "--campaign-tag",
            "wwm_0123456789ab",
            "--leave-service-stopped",
        ]
        output = io.StringIO()
        with (
            patch.object(sys, "argv", argv),
            patch.object(rotation, "STATE_ROOT", self.state_root),
            patch.object(
                rotation,
                "recover",
                return_value={"site": "webapp_fi", "phase": "already_clean"},
            ) as recover_mock,
            patch.object(rotation, "_assert_isolated_runtime"),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(rotation.main(), 0)
        recover_mock.assert_called_once_with(
            "webapp_fi",
            0,
            "wwm_0123456789ab",
            restart_service=False,
        )
        self.assertIn('"phase": "already_clean"', output.getvalue())

    def test_recover_preserves_foreign_staging_state(self):
        self.state_root.mkdir(mode=0o700)
        foreign = self.state_root / ".webapp_fi.prepare-foreign"
        foreign.mkdir(mode=0o700)
        rotation._write_metadata(
            foreign / "metadata.json",
            {
                "site": "webapp_fi",
                "campaign_tag": "wwm_abcdef012345",
                "phase": "staging",
                "staging_name": foreign.name,
            },
        )
        payload = foreign / "client.env.before"
        payload.write_bytes(b"foreign-state-must-survive\n")
        payload.chmod(0o600)
        same_campaign_unrecognized = self.state_root / (
            f".webapp_fi.prepare-wwm_0123456789ab-{'c' * 32}-ABCDEF12"
        )
        same_campaign_unrecognized.mkdir(mode=0o700)
        rotation._write_metadata(
            same_campaign_unrecognized / "metadata.json",
            {
                "site": "webapp_fi",
                "campaign_tag": "wwm_0123456789ab",
                "phase": "staging",
                "staging_name": same_campaign_unrecognized.name,
                "operation_token": "c" * 32,
            },
        )
        unrecognized_payload = same_campaign_unrecognized / "foreign.bin"
        unrecognized_payload.write_bytes(b"do-not-delete")
        unrecognized_payload.chmod(0o600)

        recovered = rotation.recover(
            "webapp_fi",
            0,
            "wwm_0123456789ab",
            self.runtime,
            self.client_dir,
            self.state_root,
        )
        self.assertEqual(recovered["phase"], "already_clean")
        self.assertEqual(recovered["cleaned_staging"], 0)
        self.assertEqual(payload.read_bytes(), b"foreign-state-must-survive\n")
        self.assertEqual(unrecognized_payload.read_bytes(), b"do-not-delete")

    def test_metadata_less_foreign_active_directory_fails_closed_without_quarantine(self):
        campaign_tag = "wwm_0123456789ab"
        runtime_before = self.runtime.read_bytes()
        self.state_root.mkdir(mode=0o700)
        active = self.state_root / "webapp_fi"
        active.mkdir(mode=0o700)
        foreign_payload = active / "unknown-state.bin"
        foreign_payload.write_bytes(b"preserve-exactly")

        with self.assertRaisesRegex(
            rotation.RotationError, "foreign or unsafe entries"
        ):
            rotation.recover(
                "webapp_fi",
                0,
                campaign_tag,
                self.runtime,
                self.client_dir,
                self.state_root,
            )
        self.assertEqual(foreign_payload.read_bytes(), b"preserve-exactly")
        self.assertEqual(self.runtime.read_bytes(), runtime_before)

    def test_owned_metadata_write_remnant_is_reclaimed_without_wedging_root(self):
        campaign_tag = "wwm_0123456789ab"
        self.state_root.mkdir(mode=0o700)
        active = self.state_root / "webapp_fi"
        active.mkdir(mode=0o700)
        remnant = active / ".metadata.json.owned123"
        remnant.write_bytes(b"incomplete non-secret metadata")
        remnant.chmod(0o600)

        recovered = rotation.recover(
            "webapp_fi",
            0,
            campaign_tag,
            self.runtime,
            self.client_dir,
            self.state_root,
        )
        self.assertEqual(recovered["phase"], "reclaimed_unclaimed")
        self.assertFalse(active.exists())
        self.assertFalse(any(self.state_root.glob(".unclaimed-*")))

        rotation.prepare(
            "webapp_fi",
            0,
            self.runtime,
            self.client_dir,
            self.state_root,
            campaign_tag=campaign_tag,
        )
        rotation.recover(
            "webapp_fi",
            0,
            campaign_tag,
            self.runtime,
            self.client_dir,
            self.state_root,
        )
        self.assertFalse(any(self.state_root.glob(".unclaimed-*")))

    def test_metadata_less_state_is_not_quarantined_after_possible_mutation(self):
        campaign_tag = "wwm_0123456789ab"
        self.state_root.mkdir(mode=0o700)
        active = self.state_root / "webapp_ir"
        active.mkdir(mode=0o700)
        scenario_key = "matrix-wwm_0123456789ab-ir"
        scenario_secret = "z" * 64
        rotation._atomic_update_env(
            self.runtime,
            changes={
                "WRITER_WITNESS_SERVICE_WEBAPP_IR_KEY_ID": scenario_key,
                "WRITER_WITNESS_SERVICE_WEBAPP_IR_SECRET": scenario_secret,
            },
        )
        rotation._atomic_update_env(
            self.client_dir / "webapp-ir.env",
            changes={
                "WRITER_WITNESS_CLIENT_KEY_ID": scenario_key,
                "WRITER_WITNESS_CLIENT_SECRET": scenario_secret,
            },
        )
        runtime_after_mutation = self.runtime.read_bytes()

        with self.assertRaisesRegex(rotation.RotationError, "activated the campaign key"):
            rotation.recover(
                "webapp_ir",
                0,
                campaign_tag,
                self.runtime,
                self.client_dir,
                self.state_root,
            )
        self.assertTrue(active.is_dir())
        self.assertEqual(self.runtime.read_bytes(), runtime_after_mutation)

    def test_metadata_less_symlink_is_rejected_without_touching_target(self):
        campaign_tag = "wwm_0123456789ab"
        self.state_root.mkdir(mode=0o700)
        target = self.root / "foreign-target"
        target.mkdir()
        payload = target / "state.bin"
        payload.write_bytes(b"untouched")
        active = self.state_root / "webapp_fi"
        active.symlink_to(target, target_is_directory=True)

        with self.assertRaisesRegex(rotation.RotationError, "private owned directory"):
            rotation.recover(
                "webapp_fi",
                0,
                campaign_tag,
                self.runtime,
                self.client_dir,
                self.state_root,
            )
        self.assertTrue(active.is_symlink())
        self.assertEqual(payload.read_bytes(), b"untouched")

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
        self.assertFalse((self.state_root / "webapp_fi").exists())

    def test_campaign_prepare_requires_and_persists_bounded_expiry(self):
        with self.assertRaisesRegex(rotation.RotationError, "missing or invalid"):
            self._original_prepare(
                "webapp_fi",
                0,
                self.runtime,
                self.client_dir,
                self.state_root,
                campaign_tag="wwm_0123456789ab",
            )

        prepared = self._original_prepare(
            "webapp_fi",
            0,
            self.runtime,
            self.client_dir,
            self.state_root,
            campaign_tag="wwm_0123456789ab",
            campaign_not_after=self.campaign_not_after,
        )
        runtime = self._values(self.runtime)
        self.assertEqual(
            runtime["WRITER_WITNESS_SERVICE_WEBAPP_FI_NOT_AFTER"],
            self.campaign_not_after,
        )
        self.assertEqual(
            runtime["WRITER_WITNESS_SERVICE_WEBAPP_FI_PREVIOUS_NOT_AFTER"],
            self.campaign_not_after,
        )
        self.assertEqual(prepared["campaign_not_after"], self.campaign_not_after)
        rotation.revoke(
            "webapp_fi", 0, self.runtime, self.client_dir, self.state_root
        )
        runtime = self._values(self.runtime)
        self.assertEqual(
            runtime["WRITER_WITNESS_SERVICE_WEBAPP_FI_NOT_AFTER"],
            self.campaign_not_after,
        )
        self.assertNotIn(
            "WRITER_WITNESS_SERVICE_WEBAPP_FI_PREVIOUS_NOT_AFTER",
            runtime,
        )
        rotation.rollback(
            "webapp_fi", 0, self.runtime, self.client_dir, self.state_root
        )
        self.assertNotIn(
            "WRITER_WITNESS_SERVICE_WEBAPP_FI_NOT_AFTER",
            self._values(self.runtime),
        )

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

    def test_campaign_recovery_handles_ambiguous_preparing_and_revoked_phases(self):
        for phase in ("preparing", "prepared", "revoked", "recovered"):
            with self.subTest(phase=phase):
                runtime_before = self.runtime.read_bytes()
                client_path = self.client_dir / "webapp-fi.env"
                client_before = client_path.read_bytes()
                prepared = rotation.prepare(
                    "webapp_fi",
                    0,
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                    campaign_tag="wwm_0123456789ab",
                )
                self.assertEqual(prepared["new_key_id"], "matrix-wwm_0123456789ab-fi")
                if phase == "revoked":
                    rotation.revoke("webapp_fi", 0, self.runtime, self.client_dir, self.state_root)
                elif phase in {"preparing", "recovered"}:
                    metadata_path = self.state_root / "webapp_fi" / "metadata.json"
                    metadata = rotation._load_metadata(metadata_path)
                    metadata["phase"] = phase
                    rotation._write_metadata(metadata_path, metadata)
                recovered = rotation.recover(
                    "webapp_fi",
                    0,
                    "wwm_0123456789ab",
                    self.runtime,
                    self.client_dir,
                    self.state_root,
                )
                self.assertEqual(recovered["phase"], "recovered")
                self.assertEqual(self.runtime.read_bytes(), runtime_before)
                self.assertEqual(client_path.read_bytes(), client_before)
                self.assertFalse((self.state_root / "webapp_fi").exists())

    def test_campaign_recovery_refuses_foreign_campaign_without_mutation(self):
        rotation.prepare(
            "webapp_ir",
            0,
            self.runtime,
            self.client_dir,
            self.state_root,
            campaign_tag="wwm_0123456789ab",
        )
        runtime_before = self.runtime.read_bytes()
        with self.assertRaisesRegex(rotation.RotationError, "different matrix campaign"):
            rotation.recover(
                "webapp_ir",
                0,
                "wwm_abcdef012345",
                self.runtime,
                self.client_dir,
                self.state_root,
            )
        self.assertEqual(self.runtime.read_bytes(), runtime_before)


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
                patch.object(smoke, "_require_isolated_runtime"),
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
