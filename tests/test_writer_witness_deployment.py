import hashlib
import fcntl
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ACTIVATION_HELPER = ROOT / "deploy/writer-witness/writer-witness-activation.py"
ACTIVATION_MODULE = runpy.run_path(str(ACTIVATION_HELPER))
ACTIVATION_MANAGED_FILES = tuple(ACTIVATION_MODULE["MANAGED_FILES"])
ACTIVATION_MANAGED_UNITS = tuple(ACTIVATION_MODULE["MANAGED_UNITS"])


class WriterWitnessDeploymentTests(unittest.TestCase):
    HOST_TOOLCHAIN_SHA256 = "f" * 64

    def _activation_run(
        self,
        root: Path,
        command: str,
        *,
        release_id: str | None = None,
        kill_after: str | None = None,
        complete_rollback: bool = True,
        unit_states: list[str] | None = None,
        host_toolchain_sha256: str | None = None,
    ) -> subprocess.CompletedProcess:
        toolchain_sha256 = host_toolchain_sha256 or self.HOST_TOOLCHAIN_SHA256
        arguments = [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-X",
            "utf8",
            "-X",
            "pycache_prefix=/dev/null",
            str(ACTIVATION_HELPER),
            "--root",
            str(root),
            command,
        ]
        if release_id is not None:
            if command == "begin":
                arguments.extend(
                    [
                        "--release-id",
                        release_id,
                        "--release-dir",
                        f"/srv/trading-bot-witness/releases/{release_id}",
                        "--venv-dir",
                        f"/opt/trading-bot-witness/venvs/{release_id}",
                        "--activation-dir",
                        f"/opt/trading-bot-witness/activations/{release_id}",
                        "--host-toolchain-inventory-sha256",
                        toolchain_sha256,
                        "--host-toolchain-verifier",
                        str(ROOT / "scripts/verify_writer_witness_host_toolchain.py"),
                        "--package-lock-helper",
                        str(ROOT / "scripts/hold_writer_witness_package_locks.py"),
                    ]
                )
            else:
                arguments.extend(["--release-id", release_id])
        if command in {"record-unit-intent", "complete-rollback"}:
            if unit_states is None:
                journal = json.loads(
                    (
                        root
                        / "var/lib/trading-bot-witness/activation-state/active.json"
                    ).read_text(encoding="utf-8")
                )
                if command == "record-unit-intent":
                    predecessor = (root / "srv/trading-bot-witness/current").exists()
                    unit_states = []
                    for unit in ACTIVATION_MANAGED_UNITS:
                        if predecessor:
                            active_state = (
                                "inactive"
                                if unit.endswith("backup.service")
                                else "active"
                            )
                            unit_file_state = (
                                "static"
                                if unit.endswith("backup.service")
                                else "enabled"
                            )
                            state = f"{unit}:loaded:{active_state}:{unit_file_state}"
                        elif unit == "nginx":
                            state = f"{unit}:loaded:inactive:disabled"
                        else:
                            state = f"{unit}:not-found:inactive:not-found"
                        unit_states.append(state)
                else:
                    unit_states = [
                        f"{unit}:{state['load_state']}:{state['active_state']}:{state['unit_file_state']}"
                        for unit, state in journal["unit_states"].items()
                    ]
            for state in unit_states:
                arguments.extend(["--unit-state", state])
        if command in {
            "record-unit-intent",
            "commit",
            "complete",
            "complete-rollback",
            "recover",
        }:
            arguments.extend(
                [
                    "--host-toolchain-inventory-sha256",
                    toolchain_sha256,
                ]
            )
        environment = {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "WRITER_WITNESS_ACTIVATION_TEST_MODE": "1",
        }
        if kill_after is not None:
            environment.update(
                {
                    "WRITER_WITNESS_ACTIVATION_ALLOW_FAILPOINTS": "1",
                    "WRITER_WITNESS_ACTIVATION_KILL_AFTER": kill_after,
                }
            )
        completed = subprocess.run(
            arguments,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        if (
            command == "recover"
            and complete_rollback
            and completed.returncode == 0
            and "activation_recovered=rolled-back-pending-service-completion"
            in completed.stdout
        ):
            journal = json.loads(
                (
                    root
                    / "var/lib/trading-bot-witness/activation-state/active.json"
                ).read_text(encoding="utf-8")
            )
            finalized = self._activation_run(
                root,
                "complete-rollback",
                release_id=str(journal["release_id"]),
            )
            if finalized.returncode != 0:
                return finalized
        return completed

    def _prepare_activation_host(self, root: Path, release_id: str) -> dict[str, Path]:
        for relative, mode in (
            ("etc/trading-bot-witness", 0o750),
            ("root/writer-witness-client-material", 0o700),
            ("etc/nginx/sites-available", 0o755),
            ("etc/nginx/sites-enabled", 0o755),
            ("etc/systemd/system", 0o755),
            ("usr/local/sbin", 0o755),
            ("opt/trading-bot-witness/activations", 0o755),
            ("opt/trading-bot-witness/venvs", 0o755),
            ("srv/trading-bot-witness/releases", 0o755),
            ("var/lib/trading-bot-witness/hmac-rotation", 0o700),
        ):
            path = root / relative
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(mode)
        rotation_lock = root / "var/lib/trading-bot-witness/hmac-rotation/.runtime.lock"
        rotation_lock.touch(mode=0o600, exist_ok=True)
        rotation_lock.chmod(0o600)

        old_release = root / "srv/trading-bot-witness/releases/legacy"
        old_release.mkdir(mode=0o755)
        (old_release / "generation").write_text("legacy\n", encoding="utf-8")
        current = root / "srv/trading-bot-witness/current"
        current.symlink_to(old_release)
        old_venv = root / "opt/trading-bot-witness/venv"
        (old_venv / "bin").mkdir(parents=True)
        (old_venv / "bin/python").write_text("legacy-python\n", encoding="utf-8")

        for item in ACTIVATION_MANAGED_FILES:
            destination = root / item.destination.lstrip("/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(f"old:{item.candidate}\n", encoding="utf-8")
            destination.chmod(item.mode)
        nginx_enabled = root / "etc/nginx/sites-enabled/writer-witness"
        nginx_enabled.symlink_to(root / "etc/nginx/sites-available/writer-witness")
        nginx_default = root / "etc/nginx/sites-enabled/default"
        nginx_default.write_text("legacy default\n", encoding="utf-8")

        release = root / f"srv/trading-bot-witness/releases/{release_id}"
        venv = root / f"opt/trading-bot-witness/venvs/{release_id}"
        activation = root / f"opt/trading-bot-witness/activations/{release_id}"
        return {
            "old_release": old_release,
            "old_venv": old_venv,
            "release": release,
            "venv": venv,
            "activation": activation,
        }

    def _begin_and_stage_activation(self, root: Path, release_id: str) -> Path:
        begun = self._activation_run(root, "begin", release_id=release_id)
        self.assertEqual(begun.returncode, 0, begun.stderr)
        candidates = Path(begun.stdout.strip())
        self.assertTrue(candidates.is_dir())
        release = root / f"srv/trading-bot-witness/releases/{release_id}"
        venv = root / f"opt/trading-bot-witness/venvs/{release_id}"
        activation = root / f"opt/trading-bot-witness/activations/{release_id}"
        release.mkdir(mode=0o755)
        venv.mkdir(mode=0o755)
        activation.mkdir(mode=0o755)
        (release / "generation").write_text(f"{release_id}\n", encoding="utf-8")
        (venv / "python").write_text(f"python:{release_id}\n", encoding="utf-8")
        (activation / "release").symlink_to(release)
        (activation / "venv").symlink_to(venv)
        for item in ACTIVATION_MANAGED_FILES:
            candidate = candidates / item.candidate
            candidate.write_text(f"new:{release_id}:{item.candidate}\n", encoding="utf-8")
            candidate.chmod(item.mode)
        recorded = self._activation_run(
            root,
            "record-unit-intent",
            release_id=release_id,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        return candidates

    def _build_release(self, destination: Path) -> str:
        # The release root itself is part of the production metadata contract.
        # TemporaryDirectory defaults to 0700, while canonical releases are 0755.
        destination.chmod(0o755)
        subprocess.run(
            ["bash", str(ROOT / "scripts/build_writer_witness_release.sh"), str(destination)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return hashlib.sha256((destination / "release-manifest.json").read_bytes()).hexdigest()

    def _verify_release(self, release: Path, expected_manifest_sha256: str):
        return subprocess.run(
            [
                "/usr/bin/env",
                "-i",
                "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
                "/usr/bin/python3.12",
                "-I",
                "-S",
                "-B",
                "-X",
                "utf8",
                "-X",
                "pycache_prefix=/dev/null",
                str(ROOT / "scripts/verify_writer_witness_release.py"),
                "--release-root",
                str(release),
                "--expected-manifest-sha256",
                expected_manifest_sha256,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    def _run_restore_input_primitive(
        self,
        *,
        state_root: Path,
        backup_root: Path,
        action: str,
        payload: bytes = b"",
        kill_after: str | None = None,
        fail_after: str | None = None,
    ):
        expected_sha256 = hashlib.sha256(payload).hexdigest()
        environment = {
            **os.environ,
            "WRITER_WITNESS_RESTORE_INTERNAL_TEST_MODE": "1",
            "WRITER_WITNESS_RESTORE_TEST_STATE_ROOT": str(state_root),
            "WRITER_WITNESS_RESTORE_TEST_BACKUP_DIR": str(backup_root),
            "WRITER_WITNESS_RESTORE_TEST_ACTION": action,
            "WRITER_WITNESS_RESTORE_TEST_EXPECTED_SHA256": expected_sha256,
        }
        if kill_after:
            environment["WRITER_WITNESS_RESTORE_TEST_KILL_AFTER"] = kill_after
        if fail_after:
            environment["WRITER_WITNESS_RESTORE_TEST_FAIL_AFTER"] = fail_after
        return subprocess.run(
            [
                "bash",
                str(ROOT / "deploy/writer-witness/writer-witness-live-restore.sh"),
                "--test-input-primitive",
            ],
            cwd=ROOT,
            env=environment,
            input=payload,
            capture_output=True,
        )

    def test_release_builder_emits_minimal_importable_integrity_checked_payload(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-release-") as destination:
            release = Path(destination)
            expected_manifest_sha256 = self._build_release(release)
            manifest = json.loads((release / "release-manifest.json").read_text())
            actual = {
                path.relative_to(release).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(release.rglob("*"))
                if path.is_file() and path.name != "release-manifest.json"
            }
            self.assertEqual(actual, manifest)
            self.assertIn("writer_witness_app.py", manifest)
            self.assertIn("deploy/writer-witness/001_initial.sql", manifest)
            self.assertIn("deploy/writer-witness/requirements.lock", manifest)
            self.assertIn("deploy/writer-witness/python-runtime.json", manifest)
            self.assertIn("deploy/writer-witness/nftables-policy.json", manifest)
            self.assertIn("deploy/writer-witness/wheelhouse.sha256", manifest)
            self.assertIn("deploy/writer-witness/writer-witness-activation.py", manifest)
            self.assertIn(
                "deploy/writer-witness/writer-witness-activation-recovery.service",
                manifest,
            )
            self.assertIn("deploy/writer-witness/writer-witness-offsite-backup.sh", manifest)
            self.assertIn("deploy/writer-witness/writer-witness-s3-put.py", manifest)
            self.assertIn("deploy/writer-witness/writer-witness-rotate-hmac.py", manifest)
            self.assertIn("deploy/writer-witness/writer-witness-live-restore.sh", manifest)
            self.assertIn("deploy/writer-witness/writer-witness-matrix-campaign.py", manifest)
            self.assertIn("deploy/writer-witness/writer-witness-matrix-host-faults.sh", manifest)
            self.assertIn("deploy/writer-witness/writer-witness-matrix-host-fault-state.py", manifest)
            self.assertIn("deploy/writer-witness/writer-witness-state-manifest.sh", manifest)
            self.assertIn("scripts/smoke_writer_witness_client.py", manifest)
            self.assertIn("scripts/run_writer_witness_clock_jump_probe.py", manifest)
            self.assertIn("scripts/verify_writer_witness_release.py", manifest)
            self.assertIn("scripts/verify_writer_witness_host_toolchain.py", manifest)
            self.assertIn("scripts/hold_writer_witness_package_locks.py", manifest)
            self.assertIn("scripts/verify_writer_witness_runtime.py", manifest)
            self.assertIn("scripts/verify_writer_witness_runtime_provenance.py", manifest)
            self.assertIn("scripts/verify_writer_witness_process_maps.py", manifest)
            self.assertIn("scripts/verify_writer_witness_wheelhouse.py", manifest)
            self.assertIn("scripts/verify_writer_witness_nftables.py", manifest)
            self.assertIn("scripts/render_writer_witness_credentials.py", manifest)
            self.assertNotIn(".env", "\n".join(manifest))
            self.assertFalse((release / "main.py").exists())
            verified = self._verify_release(release, expected_manifest_sha256)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertIn("release_manifest_attested=yes", verified.stdout)
            self.assertIn(f"release_manifest_entries={len(manifest)}", verified.stdout)
            imported = subprocess.run(
                [
                    "python3",
                    "-c",
                    "import writer_witness_app; print(writer_witness_app.app.title)",
                ],
                cwd=release,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(imported.stdout.strip(), "WebApp Writer Witness")

    def test_closed_source_gate_covers_every_release_executed_helper(self):
        gate = (ROOT / "scripts/run_writer_witness_preflight_source_gate.sh").read_text()
        wheelhouse_builder = (
            ROOT / "scripts/build_writer_witness_wheelhouse.sh"
        ).read_text()
        executed_sources = {
            "deploy/writer-witness/writer-witness-activation.py",
            "deploy/writer-witness/writer-witness-activation-watchdog.sh",
            "deploy/writer-witness/writer-witness-backup.sh",
            "deploy/writer-witness/writer-witness-live-restore.sh",
            "deploy/writer-witness/writer-witness-matrix-campaign.py",
            "deploy/writer-witness/writer-witness-matrix-host-fault-state.py",
            "deploy/writer-witness/writer-witness-matrix-host-faults.sh",
            "deploy/writer-witness/writer-witness-offsite-backup.sh",
            "deploy/writer-witness/writer-witness-restore-drill.sh",
            "deploy/writer-witness/writer-witness-rotate-hmac.py",
            "deploy/writer-witness/writer-witness-s3-put.py",
            "deploy/writer-witness/writer-witness-state-manifest.sh",
            "scripts/render_writer_witness_credentials.py",
            "scripts/hold_writer_witness_package_locks.py",
            "scripts/run_writer_witness_clock_jump_probe.py",
            "scripts/smoke_writer_witness_client.py",
            "scripts/verify_writer_witness_nftables.py",
            "scripts/verify_writer_witness_host_toolchain.py",
            "scripts/verify_writer_witness_release.py",
            "scripts/verify_writer_witness_runtime.py",
            "scripts/verify_writer_witness_runtime_provenance.py",
            "scripts/verify_writer_witness_process_maps.py",
            "scripts/verify_writer_witness_wheelhouse.py",
        }
        missing = sorted(source for source in executed_sources if source not in gate)
        self.assertEqual(missing, [])
        self.assertIn("scripts/configure_writer_witness_s3_backup.sh", gate)
        self.assertIn('"$WRITER_WITNESS_SYSTEM_PYTHON" -I -B', wheelhouse_builder)
        self.assertIn('"$WRITER_WITNESS_SYSTEM_PYTHON" -I -S -B', wheelhouse_builder)
        self.assertIn("-m pip --isolated download", wheelhouse_builder)
        self.assertNotIn("\npython3 ", wheelhouse_builder)

    def test_release_attestation_rejects_a_changed_manifested_file(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-release-") as destination:
            release = Path(destination)
            expected_manifest_sha256 = self._build_release(release)
            (release / "writer_witness_app.py").write_text("tampered\n", encoding="utf-8")
            verified = self._verify_release(release, expected_manifest_sha256)
            self.assertNotEqual(verified.returncode, 0)
            self.assertIn("release file hash mismatch: writer_witness_app.py", verified.stderr)

    def test_release_attestation_rejects_missing_and_extra_files(self):
        for mutation, expected_error in (
            (lambda release: (release / "writer_witness_app.py").unlink(), "missing manifested files"),
            (lambda release: (release / "unexpected.txt").write_text("drift\n"), "unmanifested files"),
        ):
            with self.subTest(expected_error=expected_error):
                with tempfile.TemporaryDirectory(prefix="writer-witness-release-") as destination:
                    release = Path(destination)
                    expected_manifest_sha256 = self._build_release(release)
                    mutation(release)
                    verified = self._verify_release(release, expected_manifest_sha256)
                    self.assertNotEqual(verified.returncode, 0)
                    self.assertIn(expected_error, verified.stderr)

    def test_release_attestation_rejects_unmanifested_directory_and_symlink(self):
        for mutation, expected_error in (
            (lambda release: (release / "unexpected").mkdir(), "unmanifested directories"),
            (
                lambda release: (release / "unexpected-link").symlink_to("writer_witness_app.py"),
                "contains a symlink",
            ),
        ):
            with self.subTest(expected_error=expected_error):
                with tempfile.TemporaryDirectory(prefix="writer-witness-release-") as destination:
                    release = Path(destination)
                    expected_manifest_sha256 = self._build_release(release)
                    mutation(release)
                    verified = self._verify_release(release, expected_manifest_sha256)
                    self.assertNotEqual(verified.returncode, 0)
                    self.assertIn(expected_error, verified.stderr)

    def test_release_attestation_rejects_a_manifest_not_bound_to_the_build(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-release-") as destination:
            release = Path(destination)
            expected_manifest_sha256 = self._build_release(release)
            manifest_path = release / "release-manifest.json"
            manifest_path.write_text(manifest_path.read_text() + "\n", encoding="utf-8")
            verified = self._verify_release(release, expected_manifest_sha256)
            self.assertNotEqual(verified.returncode, 0)
            self.assertIn("does not match the expected build manifest", verified.stderr)

    def test_systemd_unit_is_single_worker_loopback_and_hardened(self):
        unit = (ROOT / "deploy/writer-witness/writer-witness.service").read_text()
        recovery = (
            ROOT
            / "deploy/writer-witness/writer-witness-activation-recovery.service"
        ).read_text()
        watchdog_unit = (
            ROOT
            / "deploy/writer-witness/writer-witness-activation-watchdog.service"
        ).read_text()
        watchdog_script = (
            ROOT
            / "deploy/writer-witness/writer-witness-activation-watchdog.sh"
        ).read_text()
        self.assertIn("User=writer-witness", unit)
        self.assertIn("--host 127.0.0.1 --port 8011 --workers 1", unit)
        self.assertIn("EnvironmentFile=/etc/trading-bot-witness/runtime.env", unit)
        self.assertIn("WorkingDirectory=/opt/trading-bot-witness/active/release", unit)
        self.assertIn("ExecStart=/opt/trading-bot-witness/active/venv/bin/python -I -B", unit)
        self.assertIn("-m uvicorn", unit)
        self.assertIn("UnsetEnvironment=PYTHONPATH PYTHONHOME", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertNotIn("0.0.0.0", unit)
        self.assertIn("Before=nginx.service writer-witness.service", recovery)
        self.assertIn("RequiredBy=nginx.service writer-witness.service", recovery)
        self.assertIn(
            "ExecStart=/usr/bin/python3.12 -I -S -B",
            recovery,
        )
        self.assertIn("recover-boot", recovery)
        self.assertIn("KillMode=control-group", recovery)
        self.assertNotIn("writer-witness-activation-watchdog", recovery)
        self.assertNotIn("systemctl", recovery)
        self.assertIn(
            "After=local-fs.target nginx.service writer-witness.service",
            watchdog_unit,
        )
        self.assertIn('flock -n "$provision_lock_fd"', watchdog_script)
        self.assertIn('flock -n "$rotation_lock_fd"', watchdog_script)
        self.assertIn("pending-toolchain-binding", watchdog_script)
        self.assertIn("recovery-package-lock-holder.py", watchdog_script)
        self.assertIn("--assert-parent-locks", watchdog_script)
        self.assertIn("--exec /bin/bash", watchdog_script)
        self.assertIn("recovery-host-toolchain-verifier.py", watchdog_script)
        self.assertIn("host_toolchain_inventory_sha256", watchdog_script)
        self.assertLess(
            watchdog_script.index('flock -n "$rotation_lock_fd"'),
            watchdog_script.index('result="$('
                '"${activation_helper[@]}" recover'),
        )
        self.assertLess(
            watchdog_script.index("attest_recovery_toolchain"),
            watchdog_script.index('result="$('
                '"${activation_helper[@]}" recover'),
        )
        self.assertIn("Environment=PYTHONPYCACHEPREFIX=/dev/null", unit)
        for forbidden in ("BASH_ENV", "SHELLOPTS", "LD_AUDIT", "GLIBC_TUNABLES"):
            self.assertIn(forbidden, unit)
            self.assertIn(forbidden, recovery)
            self.assertIn(forbidden, watchdog_unit)
        for activation_test_variable in (
            "WRITER_WITNESS_ACTIVATION_TEST_MODE",
            "WRITER_WITNESS_ACTIVATION_ALLOW_FAILPOINTS",
            "WRITER_WITNESS_ACTIVATION_KILL_AFTER",
        ):
            self.assertIn(activation_test_variable, recovery)
            self.assertIn(activation_test_variable, watchdog_unit)
        self.assertIn(
            "ExecStart=/bin/bash /usr/local/sbin/writer-witness-activation-watchdog",
            watchdog_unit,
        )
        self.assertIn("KillMode=control-group", watchdog_unit)

    def test_provision_refuses_shell_trace_without_exposing_secret_environment(self):
        sentinel = "must-never-appear-writer-witness-secret"
        completed = subprocess.run(
            ["/bin/bash", "-x", str(ROOT / "scripts/provision_writer_witness_host.sh")],
            cwd=ROOT,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "WRITER_WITNESS_SECRET_SENTINEL": sentinel,
            },
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn(sentinel, completed.stdout + completed.stderr)
        self.assertIn("SOURCE_DIR is required", completed.stderr)

    def test_nginx_exposes_only_fixed_private_control_paths(self):
        config = (ROOT / "deploy/writer-witness/nginx.conf.template").read_text()
        self.assertIn("allow __WEBAPP_FI_SOURCE_IP__;", config)
        self.assertIn("allow __WEBAPP_IR_SOURCE_IP__;", config)
        self.assertIn("allow __WITNESS_PUBLIC_IP__;", config)
        self.assertIn("deny all;", config)
        self.assertIn("location = /v1/writer-witness/status", config)
        self.assertIn("location = /v1/writer-witness/transitions", config)
        self.assertIn("client_max_body_size 16k;", config)
        self.assertNotIn("Access-Control-Allow-Origin", config)

    def test_activation_rejects_unit_states_that_rollback_cannot_restore(self):
        parse_unit_states = ACTIVATION_MODULE["_parse_unit_states"]
        activation_error = ACTIVATION_MODULE["ActivationError"]
        values = [
            f"{unit}:loaded:inactive:disabled" for unit in ACTIVATION_MANAGED_UNITS
        ]
        values[0] = f"{ACTIVATION_MANAGED_UNITS[0]}:error:inactive:disabled"
        with self.assertRaisesRegex(activation_error, "unsupported"):
            parse_unit_states(values, required=True)

        values = [
            f"{unit}:loaded:inactive:disabled" for unit in ACTIVATION_MANAGED_UNITS
        ]
        values[0] = f"{ACTIVATION_MANAGED_UNITS[0]}:loaded:activating:enabled"
        with self.assertRaisesRegex(activation_error, "unsupported"):
            parse_unit_states(values, required=True)

        backup_index = ACTIVATION_MANAGED_UNITS.index("writer-witness-backup.service")
        values[0] = f"{ACTIVATION_MANAGED_UNITS[0]}:loaded:active:enabled"
        values[backup_index] = "writer-witness-backup.service:loaded:active:static"
        with self.assertRaisesRegex(activation_error, "unsupported"):
            parse_unit_states(values, required=True)

        values[0] = f"{ACTIVATION_MANAGED_UNITS[0]}:not-found:inactive:enabled"
        with self.assertRaisesRegex(activation_error, "unsupported"):
            parse_unit_states(values, required=True)

    def test_activation_cannot_publish_before_late_unit_intent_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-late-intent-") as directory:
            root = Path(directory)
            release_id = "late-intent"
            self._prepare_activation_host(root, release_id)
            begun = self._activation_run(root, "begin", release_id=release_id)
            self.assertEqual(begun.returncode, 0, begun.stderr)
            refused = self._activation_run(root, "publish", release_id=release_id)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("unit intent is not finalized", refused.stderr)
            recovered = self._activation_run(root, "recover")
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertIn("rolled-back-without-service-changes", recovered.stdout)

    def test_activation_recovery_helpers_are_copied_and_journal_bound_at_begin(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-recovery-binding-") as raw:
            root = Path(raw)
            release_id = "recovery-binding"
            self._prepare_activation_host(root, release_id)
            begun = self._activation_run(root, "begin", release_id=release_id)
            self.assertEqual(begun.returncode, 0, begun.stderr)
            candidates = Path(begun.stdout.strip())
            binding = self._activation_run(root, "pending-toolchain-binding")
            self.assertEqual(binding.returncode, 0, binding.stderr)
            release, digest, observed_candidates = binding.stdout.strip().split("|")
            self.assertEqual(release, release_id)
            self.assertEqual(digest, self.HOST_TOOLCHAIN_SHA256)
            self.assertEqual(Path(observed_candidates), candidates)
            verifier = candidates / "recovery-host-toolchain-verifier.py"
            self.assertEqual(verifier.stat().st_mode & 0o777, 0o700)
            verifier.write_text("tampered\n", encoding="utf-8")
            refused = self._activation_run(root, "pending-toolchain-binding")
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("differs from its journal", refused.stderr)

    def test_no_service_terminal_archive_recovers_after_every_hard_kill(self):
        failpoints = (
            "rollback_without_service_terminal_recorded",
            "archive_history_published",
            "archive_before_journal_unlink",
            "archive_journal_unlinked",
            "archive_journal_fsynced",
            "archive_operation_removed",
            "archive_operation_fsynced",
        )
        for failpoint in failpoints:
            with self.subTest(failpoint=failpoint):
                with tempfile.TemporaryDirectory(
                    prefix="writer-witness-terminal-archive-"
                ) as directory:
                    root = Path(directory)
                    release_id = "terminal-retry"
                    paths = self._prepare_activation_host(root, release_id)
                    begun = self._activation_run(root, "begin", release_id=release_id)
                    self.assertEqual(begun.returncode, 0, begun.stderr)

                    killed = self._activation_run(
                        root,
                        "recover",
                        kill_after=failpoint,
                        complete_rollback=False,
                    )
                    self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)

                    recovered = self._activation_run(root, "recover")
                    self.assertEqual(recovered.returncode, 0, recovered.stderr)
                    repeated = self._activation_run(root, "recover")
                    self.assertEqual(repeated.returncode, 0, repeated.stderr)
                    self.assertIn("activation_recovered=no", repeated.stdout)
                    self.assertFalse(
                        (
                            root
                            / "var/lib/trading-bot-witness/activation-state/active.json"
                        ).exists()
                    )
                    history = list(
                        (
                            root
                            / "var/lib/trading-bot-witness/activation-state/history"
                        ).glob("*-rolled_back_without_service_changes.json")
                    )
                    self.assertEqual(len(history), 1)
                    self.assertFalse(paths["release"].exists())
                    self.assertFalse(paths["venv"].exists())
                    self.assertFalse(paths["activation"].exists())
                    self.assertEqual(
                        (root / "srv/trading-bot-witness/current").resolve(),
                        paths["old_release"].resolve(),
                    )

                    retried = self._activation_run(root, "begin", release_id=release_id)
                    self.assertEqual(retried.returncode, 0, retried.stderr)
                    cleaned = self._activation_run(root, "recover")
                    self.assertEqual(cleaned.returncode, 0, cleaned.stderr)

    def test_activation_rollback_completion_requires_exact_observed_unit_state(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-exact-intent-") as directory:
            root = Path(directory)
            release_id = "exact-intent"
            self._prepare_activation_host(root, release_id)
            self._begin_and_stage_activation(root, release_id)
            killed = self._activation_run(
                root,
                "publish",
                release_id=release_id,
                kill_after="new_active_switched",
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL)
            recovered = self._activation_run(root, "recover", complete_rollback=False)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            journal = json.loads(
                (
                    root / "var/lib/trading-bot-witness/activation-state/active.json"
                ).read_text(encoding="utf-8")
            )
            observed = [
                f"{unit}:{state['load_state']}:{state['active_state']}:{state['unit_file_state']}"
                for unit, state in journal["unit_states"].items()
            ]
            observed[0] = "nginx:loaded:inactive:enabled"
            refused = self._activation_run(
                root,
                "complete-rollback",
                release_id=release_id,
                unit_states=observed,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("does not match intent", refused.stderr)
            completed = self._activation_run(
                root, "complete-rollback", release_id=release_id
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_provisioning_keeps_webapp_and_cdn_activation_out_of_scope(self):
        script = (ROOT / "scripts/provision_writer_witness_host.sh").read_text()
        toolchain_verifier = (
            ROOT / "scripts/verify_writer_witness_host_toolchain.py"
        ).read_text()
        self.assertNotIn("apt-get", script)
        self.assertNotIn("useradd", script)
        self.assertIn("WRITER_WITNESS_EXPECTED_HOST_TOOLCHAIN_INVENTORY_SHA256", script)
        self.assertIn("verify_writer_witness_host_toolchain.py", script)
        self.assertIn("/run/lock/writer-witness-provision.lock", script)
        self.assertLess(
            script.index('flock -n "$outer_provision_lock_fd"'),
            script.index('install -d -m 0755 -o root -g root "$release_dir"'),
        )
        self.assertIn("GRANT SELECT, UPDATE ON webapp_writer_witness_state", script)
        self.assertIn("GRANT SELECT, INSERT ON webapp_writer_witness_receipts", script)
        self.assertNotIn("ufw allow from", script)
        self.assertIn("Firewall mutation is intentionally outside release activation", script)
        self.assertNotIn("ufw allow OpenSSH", script)
        self.assertIn('"webapp_flags_changed":false', script)
        self.assertIn('"cdn_changed":false', script)
        self.assertNotIn("WRITER_WITNESS_REQUIRED=true", script)
        self.assertNotIn("arvan", script.lower())
        self.assertIn("PermitRootLogin prohibit-password", script)
        self.assertIn("PasswordAuthentication no", script)
        self.assertIn('WRITER_WITNESS_HARDEN_SSH:-false', script)
        self.assertIn("writer-witness-rotate-hmac", script)
        self.assertIn("writer-witness-matrix-host-fault-state", script)
        self.assertIn("writer-witness-matrix-campaign", script)
        self.assertIn("writer-witness-offsite-backup", script)
        self.assertIn("writer-witness-s3-put", script)
        self.assertIn("matrix-campaign/authorization-intents", script)
        self.assertIn("matrix-campaign/authorizations", script)
        self.assertIn("libfaketime", toolchain_verifier)
        self.assertIn("verify_writer_witness_wheelhouse.py", script)
        self.assertIn("wheelhouse.sha256", script)
        self.assertIn("--no-index", script)
        self.assertIn('--find-links "$WHEELHOUSE"', script)
        self.assertIn("--no-compile", script)
        self.assertIn('"$expected_python_path" -I -S -B -X utf8', script)
        self.assertIn('-m venv --without-pip "$venv_dir"', script)
        self.assertIn("pip-24.0-py3-none-any.whl", script)
        self.assertIn("runpy.run_module", script)
        self.assertIn(
            '"$venv_dir/bin/python" -I -B -X utf8 -X pycache_prefix=/dev/null -c',
            script,
        )
        self.assertIn("include-system-site-packages = false", script)
        self.assertNotIn("PIP_CONFIG_FILE=", script)
        self.assertLess(
            script.index("runtime_attestation_before_check="),
            script.index('sys.argv=["pip","check"]'),
        )
        self.assertNotIn('"$venv_dir/bin/python" -m pip check', script)
        self.assertIn("/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin", script)
        self.assertIn("-I -S -B -X utf8 -X pycache_prefix=/dev/null", script)
        self.assertIn("--system-only", script)
        self.assertIn("--system-runtime-manifest", script)
        self.assertIn("--expected-system-runtime-manifest-sha256", script)
        self.assertIn('"schema_version": "writer_witness_runtime_provenance_v3"', script)
        self.assertIn('"host_toolchain_inventory_sha256"', script)
        self.assertIn("basicConstraints=critical,CA:TRUE,pathlen:0", script)
        self.assertIn("keyUsage=critical,keyCertSign,cRLSign", script)
        self.assertIn("extendedKeyUsage=serverAuth", script)
        self.assertIn(
            '- "$private_key_file" "$public_key_file" "$signing_init_root"',
            script,
        )
        self.assertIn("signing-key-initialization", script)
        self.assertIn("WRITER_WITNESS_ROTATE_TLS:-false", script)
        self.assertIn("requirements.lock", script)
        self.assertIn("WRITER_WITNESS_EXPECTED_MANIFEST_SHA256", script)
        self.assertIn("--expected-uid 0", script)
        self.assertIn("--expected-gid 0", script)
        self.assertIn('bootstrap_attest_release "$release_dir"', script)
        self.assertIn('SOURCE_DIR="$release_dir"', script)
        self.assertIn("verify_writer_witness_runtime.py", script)
        self.assertIn("--runtime-prefix", script)
        self.assertIn('"$venv_dir/bin/python" -I -S', script)
        self.assertIn("verify_writer_witness_runtime_provenance.py", script)
        self.assertIn("runtime-provenance.json", script)
        self.assertIn("os.fchmod(descriptor, 0o644)", script)
        self.assertIn("verify_writer_witness_nftables.py", script)
        self.assertIn("nftables-policy.json", script)
        self.assertIn("nft -j list ruleset", script)
        self.assertIn(
            '"$release_dir/scripts/verify_writer_witness_nftables.py"',
            script,
        )
        self.assertIn("/opt/trading-bot-witness/venvs/$RELEASE_ID", script)
        self.assertIn("/opt/trading-bot-witness/activations", script)
        self.assertIn("/opt/trading-bot-witness/active/release", script)
        self.assertIn("/opt/trading-bot-witness/active/venv", script)
        self.assertIn("assert_no_writer_witness_systemd_dropins", script)
        self.assertIn("fsync_trees", script)
        self.assertIn('fsync_trees "$release_dir" "$venv_dir" "$activation_dir"', script)
        self.assertIn("atomic_install_file", script)
        self.assertIn("writer-witness-activation-recovery.service", script)
        self.assertIn("rollback_activation_transaction", script)
        self.assertIn("activation_exit_guard", script)
        self.assertIn("trap 'activation_exit_guard $?' EXIT", script)
        self.assertIn("trap 'rollback_activation_transaction 129' HUP", script)
        self.assertIn("installed_activation begin", script)
        self.assertIn("installed_activation publish", script)
        self.assertIn("installed_activation commit", script)
        self.assertIn("installed_activation recover", script)
        self.assertLess(
            script.index("activation_transaction_open=true"),
            script.index('activation_candidates="$(installed_activation begin'),
        )
        self.assertIn('--runtime-env "$activation_candidates/runtime.env"', script)
        self.assertIn('nginx_target="$activation_candidates/nginx-writer-witness"', script)
        self.assertIn("render_writer_witness_credentials.py", script)
        self.assertEqual(
            script.count(
                '"$release_dir/scripts/render_writer_witness_credentials.py"'
            ),
            3,
        )
        self.assertIn('"$SOURCE_DIR/scripts/render_writer_witness_credentials.py"', script)
        self.assertIn("--mode initialize-bootstrap", script)
        self.assertIn("--mode database-env", script)
        self.assertIn("postgres_scram_verifier", script)
        self.assertIn("SCRAM-SHA-256$", script)
        self.assertIn("PASSWORD '$WITNESS_DB_MIGRATOR_VERIFIER'", script)
        self.assertIn("PASSWORD '$WITNESS_DB_RUNTIME_VERIFIER'", script)
        self.assertNotIn("PASSWORD '$WITNESS_DB_MIGRATOR_PASSWORD'", script)
        self.assertNotIn("PASSWORD '$WITNESS_DB_RUNTIME_PASSWORD'", script)
        self.assertIn("--mode prepare", script)
        self.assertIn("--mode finalize", script)
        self.assertIn('--rotation-lock-fd "$rotation_lock_fd"', script)
        self.assertIn(
            'args.mode in {"prepare", "finalize"} and args.rotation_lock_fd is None',
            (ROOT / "scripts/render_writer_witness_credentials.py").read_text(),
        )
        self.assertIn("flock -n \"$rotation_lock_fd\"", script)
        self.assertNotIn('source "$secrets_file"', script)
        self.assertNotIn("WITNESS_FI_HMAC_SECRET", script)
        self.assertNotIn("WITNESS_IR_HMAC_SECRET", script)
        self.assertLess(
            script.index("flock -n \"$rotation_lock_fd\""),
            script.index("installed_activation begin"),
        )
        self.assertLess(
            script.index("--mode prepare"),
            script.index("installed_activation publish"),
        )
        self.assertLess(
            script.index("--mode finalize"),
            script.index("installed_activation commit"),
        )
        self.assertLess(
            script.index("installed_activation commit"),
            script.rindex("installed_activation complete"),
        )
        self.assertIn("systemctl show -p FragmentPath", script)
        self.assertIn("systemctl show -p DropInPaths", script)
        self.assertIn("MemoryDenyWriteExecute:yes", script)
        self.assertLess(
            script.index("nft -j list ruleset"),
            script.index("installed_activation publish"),
        )
        self.assertLess(
            script.index('systemctl stop "$unit"'),
            script.index("installed_activation publish"),
        )
        self.assertIn('systemctl stop "$unit"', script)
        self.assertIn('writer-witness-backup.service', script)
        self.assertIn('writer-witness-offsite-backup.service', script)
        self.assertIn('systemctl mask --runtime "$unit"', script)
        self.assertIn('ActiveState --value "$unit"', script)
        self.assertIn("preserves failed oneshot evidence", script)
        snapshot = script[script.index("activation_unit_state_args=()") :]
        self.assertNotIn(
            "active|activating|deactivating|inactive|failed) active_state=inactive",
            snapshot,
        )
        wait_loop = snapshot[
            snapshot.index(
                "for unit in writer-witness-backup.service writer-witness-offsite-backup.service"
            ) :
        ]
        self.assertNotIn('systemctl reset-failed "$unit"', wait_loop)
        self.assertIn("activation could not quiesce", script)
        self.assertLess(
            script.index("installed_activation publish"),
            script.index("systemctl show -p FragmentPath"),
        )
        self.assertNotIn('cat >/etc/trading-bot-witness/runtime.env', script)
        self.assertNotIn('>"/etc/nginx/sites-available/writer-witness"', script)
        self.assertIn('/var/lib/trading-bot-witness/restore-state', script)
        self.assertIn(
            'Path("/var/lib/trading-bot-witness/matrix-campaign/.campaign.lock")',
            script,
        )

    def test_activation_initial_migration_commits_one_code_runtime_generation(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-activation-") as directory:
            root = Path(directory)
            release_id = "release-one"
            paths = self._prepare_activation_host(root, release_id)
            self._begin_and_stage_activation(root, release_id)

            published = self._activation_run(root, "publish", release_id=release_id)
            self.assertEqual(published.returncode, 0, published.stderr)
            active = root / "opt/trading-bot-witness/active"
            current = root / "srv/trading-bot-witness/current"
            compatibility_venv = root / "opt/trading-bot-witness/venv"
            self.assertEqual(active.resolve(), paths["activation"].resolve())
            self.assertEqual(current.resolve(), paths["release"].resolve())
            self.assertEqual(compatibility_venv.resolve(), paths["venv"].resolve())
            legacy_activation = (
                root
                / f"opt/trading-bot-witness/activations/legacy-before-{release_id}"
            )
            self.assertEqual(
                (legacy_activation / "release").resolve(), paths["old_release"].resolve()
            )
            self.assertIn("legacy-before", str((legacy_activation / "venv").resolve()))
            for item in ACTIVATION_MANAGED_FILES:
                destination = root / item.destination.lstrip("/")
                self.assertEqual(
                    destination.read_text(encoding="utf-8"),
                    f"new:{release_id}:{item.candidate}\n",
                )

            committed = self._activation_run(root, "commit", release_id=release_id)
            self.assertEqual(committed.returncode, 0, committed.stderr)
            completed = self._activation_run(root, "complete", release_id=release_id)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(
                (root / "var/lib/trading-bot-witness/activation-state/active.json").exists()
            )
            repeated = self._activation_run(root, "recover")
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertIn("activation_recovered=no", repeated.stdout)
            self.assertEqual(active.resolve(), paths["activation"].resolve())

    def test_activation_fresh_install_sigkill_rolls_back_to_no_generation_and_retries(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-activation-fresh-") as directory:
            root = Path(directory)
            release_id = "fresh-release"
            for relative, mode in (
                ("etc/trading-bot-witness", 0o750),
                ("root/writer-witness-client-material", 0o700),
                ("etc/nginx/sites-available", 0o755),
                ("etc/nginx/sites-enabled", 0o755),
                ("etc/systemd/system", 0o755),
                ("usr/local/sbin", 0o755),
                ("opt/trading-bot-witness/activations", 0o755),
                ("opt/trading-bot-witness/venvs", 0o755),
                ("srv/trading-bot-witness/releases", 0o755),
            ):
                path = root / relative
                path.mkdir(parents=True, exist_ok=True)
                path.chmod(mode)

            self._begin_and_stage_activation(root, release_id)
            killed = self._activation_run(
                root,
                "publish",
                release_id=release_id,
                kill_after="activation_published",
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)
            recovered = self._activation_run(root, "recover")
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertIn(
                "activation_recovered=rolled-back-pending-service-completion",
                recovered.stdout,
            )
            for relative in (
                "opt/trading-bot-witness/active",
                "srv/trading-bot-witness/current",
                "opt/trading-bot-witness/venv",
                f"opt/trading-bot-witness/activations/{release_id}",
                f"opt/trading-bot-witness/venvs/{release_id}",
                f"srv/trading-bot-witness/releases/{release_id}",
            ):
                self.assertFalse((root / relative).exists(), relative)

            self._begin_and_stage_activation(root, release_id)
            published = self._activation_run(root, "publish", release_id=release_id)
            self.assertEqual(published.returncode, 0, published.stderr)
            committed = self._activation_run(root, "commit", release_id=release_id)
            self.assertEqual(committed.returncode, 0, committed.stderr)
            completed = self._activation_run(root, "complete", release_id=release_id)
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_activation_rollback_restores_pre_finalize_bootstrap_and_marker(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-activation-credentials-") as directory:
            root = Path(directory)
            release_id = "credential-rollback"
            self._prepare_activation_host(root, release_id)
            bootstrap = root / "etc/trading-bot-witness/bootstrap-secrets.env"
            marker = (
                root
                / "var/lib/trading-bot-witness/activation-state/credential-state.json"
            )
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.parent.chmod(0o700)
            bootstrap.write_text("database=old\nhmac=old\n", encoding="utf-8")
            marker.write_text('{"initialized":false}\n', encoding="utf-8")
            bootstrap.chmod(0o600)
            marker.chmod(0o600)

            self._begin_and_stage_activation(root, release_id)
            published = self._activation_run(root, "publish", release_id=release_id)
            self.assertEqual(published.returncode, 0, published.stderr)
            bootstrap.write_text("database=old\n", encoding="utf-8")
            marker.write_text('{"initialized":true}\n', encoding="utf-8")

            recovered = self._activation_run(root, "recover")
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertEqual(
                bootstrap.read_text(encoding="utf-8"), "database=old\nhmac=old\n"
            )
            self.assertEqual(
                marker.read_text(encoding="utf-8"), '{"initialized":false}\n'
            )

    def test_activation_initial_sigkill_failpoints_recover_and_allow_same_release_retry(self):
        failpoints = (
            "begin_journal",
            "candidates_bound",
            "legacy_venv_moved",
            "legacy_activation_fsynced",
            "legacy_active_switched",
            *(f"candidate_published_{item.candidate}" for item in ACTIVATION_MANAGED_FILES),
            "nginx_enabled_published",
            "nginx_default_removed",
            "candidates_published",
            "new_active_switched",
            "activation_published",
        )
        for kill_after in failpoints:
            with self.subTest(kill_after=kill_after):
                with tempfile.TemporaryDirectory(
                    prefix="writer-witness-activation-kill-"
                ) as directory:
                    root = Path(directory)
                    release_id = "retry-release"
                    paths = self._prepare_activation_host(root, release_id)
                    if kill_after == "begin_journal":
                        killed = self._activation_run(
                            root,
                            "begin",
                            release_id=release_id,
                            kill_after=kill_after,
                        )
                    else:
                        self._begin_and_stage_activation(root, release_id)
                        killed = self._activation_run(
                            root,
                            "publish",
                            release_id=release_id,
                            kill_after=kill_after,
                        )
                    self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)

                    recovered = self._activation_run(root, "recover")
                    self.assertEqual(recovered.returncode, 0, recovered.stderr)
                    repeated = self._activation_run(root, "recover")
                    self.assertEqual(repeated.returncode, 0, repeated.stderr)
                    self.assertIn("activation_recovered=no", repeated.stdout)
                    active = root / "opt/trading-bot-witness/active"
                    if kill_after in {"begin_journal", "candidates_bound"}:
                        self.assertFalse(active.exists())
                        self.assertEqual(
                            (root / "srv/trading-bot-witness/current").resolve(),
                            paths["old_release"].resolve(),
                        )
                    else:
                        self.assertIn("legacy-before", str(active.resolve()))
                        self.assertEqual(
                            (root / "srv/trading-bot-witness/current").resolve(),
                            paths["old_release"].resolve(),
                        )
                    for item in ACTIVATION_MANAGED_FILES:
                        destination = root / item.destination.lstrip("/")
                        self.assertEqual(
                            destination.read_text(encoding="utf-8"),
                            f"old:{item.candidate}\n",
                        )
                    self.assertFalse(paths["release"].exists())
                    self.assertFalse(paths["venv"].exists())
                    self.assertFalse(paths["activation"].exists())

                    # Cleanup is ownership-bound, so the exact release id is reusable.
                    retried = self._activation_run(root, "begin", release_id=release_id)
                    self.assertEqual(retried.returncode, 0, retried.stderr)
                    cleaned = self._activation_run(root, "recover")
                    self.assertEqual(cleaned.returncode, 0, cleaned.stderr)

    def test_nonterminal_recovery_rejects_digest_drift_before_any_state_change(self):
        setups = ("prepared", "unit-intent", "publishing", "activated", "committed")
        for durable_phase in setups:
            with self.subTest(durable_phase=durable_phase), tempfile.TemporaryDirectory(
                prefix="writer-witness-recovery-toolchain-drift-"
            ) as directory:
                root = Path(directory)
                release_id = f"digest-{durable_phase}"
                self._prepare_activation_host(root, release_id)
                if durable_phase == "prepared":
                    begun = self._activation_run(root, "begin", release_id=release_id)
                    self.assertEqual(begun.returncode, 0, begun.stderr)
                else:
                    self._begin_and_stage_activation(root, release_id)
                    if durable_phase == "publishing":
                        killed = self._activation_run(
                            root,
                            "publish",
                            release_id=release_id,
                            kill_after="new_active_switched",
                        )
                        self.assertEqual(killed.returncode, -signal.SIGKILL)
                    elif durable_phase in {"activated", "committed"}:
                        published = self._activation_run(
                            root, "publish", release_id=release_id
                        )
                        self.assertEqual(published.returncode, 0, published.stderr)
                        if durable_phase == "committed":
                            committed = self._activation_run(
                                root, "commit", release_id=release_id
                            )
                            self.assertEqual(committed.returncode, 0, committed.stderr)
                state_root = root / "var/lib/trading-bot-witness/activation-state"
                journal = state_root / "active.json"
                before_journal = journal.read_bytes()
                current = root / "srv/trading-bot-witness/current"
                active = root / "opt/trading-bot-witness/active"
                before_current = os.readlink(current) if current.is_symlink() else None
                before_active = os.readlink(active) if active.is_symlink() else None
                rejected = self._activation_run(
                    root,
                    "recover",
                    complete_rollback=False,
                    host_toolchain_sha256="e" * 64,
                )
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("journal binding", rejected.stderr)
                self.assertEqual(journal.read_bytes(), before_journal)
                self.assertEqual(
                    os.readlink(current) if current.is_symlink() else None,
                    before_current,
                )
                self.assertEqual(
                    os.readlink(active) if active.is_symlink() else None,
                    before_active,
                )
                recovered = self._activation_run(root, "recover")
                self.assertEqual(recovered.returncode, 0, recovered.stderr)

    def test_activation_subsequent_sigkill_rolls_back_exact_previous_pair(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-activation-next-") as directory:
            root = Path(directory)
            first = "release-one"
            first_paths = self._prepare_activation_host(root, first)
            self._begin_and_stage_activation(root, first)
            self.assertEqual(
                self._activation_run(root, "publish", release_id=first).returncode, 0
            )
            self.assertEqual(
                self._activation_run(root, "commit", release_id=first).returncode, 0
            )
            self.assertEqual(
                self._activation_run(root, "complete", release_id=first).returncode, 0
            )

            second = "release-two"
            second_release = root / f"srv/trading-bot-witness/releases/{second}"
            second_venv = root / f"opt/trading-bot-witness/venvs/{second}"
            second_activation = root / f"opt/trading-bot-witness/activations/{second}"
            self._begin_and_stage_activation(root, second)
            killed = self._activation_run(
                root,
                "publish",
                release_id=second,
                kill_after="new_active_switched",
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)
            recovered = self._activation_run(root, "recover")
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertEqual(
                (root / "opt/trading-bot-witness/active").resolve(),
                first_paths["activation"].resolve(),
            )
            self.assertEqual(
                (root / "srv/trading-bot-witness/current").resolve(),
                first_paths["release"].resolve(),
            )
            self.assertEqual(
                (root / "opt/trading-bot-witness/venv").resolve(),
                first_paths["venv"].resolve(),
            )
            for item in ACTIVATION_MANAGED_FILES:
                destination = root / item.destination.lstrip("/")
                self.assertEqual(
                    destination.read_text(encoding="utf-8"),
                    f"new:{first}:{item.candidate}\n",
                )
            self.assertFalse(second_release.exists())
            self.assertFalse(second_venv.exists())
            self.assertFalse(second_activation.exists())

    def test_activation_helper_rejects_non_isolated_python_startup(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-activation-runtime-") as directory:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ACTIVATION_HELPER),
                    "--root",
                    directory,
                    "recover",
                ],
                cwd=ROOT,
                env={
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                    "WRITER_WITNESS_ACTIVATION_TEST_MODE": "1",
                },
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("startup is not isolated", completed.stderr)

    def test_activation_protocol_adapter_is_bound_to_exact_predecessor_helpers(self):
        provision = (ROOT / "scripts/provision_writer_witness_host.sh").read_text(
            encoding="utf-8"
        )
        for revision, expected in (
            (
                "5bd5c884",
                "271994f11950d2848360a59dfd080b9856ba01ecd966e212b9e1c5d8fc49e1ea",
            ),
            (
                "2e4dc0b1",
                "7142c88933f4b6eb355acb066d2045bb083f148ac804d80ba34296d18fc987d6",
            ),
        ):
            completed = subprocess.run(
                [
                    "/usr/bin/git",
                    "show",
                    f"{revision}:deploy/writer-witness/writer-witness-activation.py",
                ],
                cwd=ROOT,
                capture_output=True,
                check=True,
            )
            self.assertEqual(hashlib.sha256(completed.stdout).hexdigest(), expected)
            self.assertIn(expected, provision)
        self.assertIn("legacy activation recovery requires explicit operator authorization", provision)
        self.assertIn("complete_installed_activation_protocol", provision)
        with tempfile.TemporaryDirectory(prefix="writer-witness-protocol-") as directory:
            current = self._activation_run(Path(directory), "protocol-version")
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertEqual(
            current.stdout.strip(), "writer_witness_activation_protocol_v2"
        )

    def test_activation_recovery_itself_is_repeatable_across_sigkill(self):
        for rollback_kill in ("rollback_active_switched", "rollback_restored"):
            with self.subTest(rollback_kill=rollback_kill):
                with tempfile.TemporaryDirectory(
                    prefix="writer-witness-activation-recovery-kill-"
                ) as directory:
                    root = Path(directory)
                    release_id = "failed-release"
                    paths = self._prepare_activation_host(root, release_id)
                    self._begin_and_stage_activation(root, release_id)
                    killed_publish = self._activation_run(
                        root,
                        "publish",
                        release_id=release_id,
                        kill_after="new_active_switched",
                    )
                    self.assertEqual(killed_publish.returncode, -signal.SIGKILL)
                    killed_recovery = self._activation_run(
                        root, "recover", kill_after=rollback_kill
                    )
                    self.assertEqual(killed_recovery.returncode, -signal.SIGKILL)
                    recovered = self._activation_run(root, "recover")
                    self.assertEqual(recovered.returncode, 0, recovered.stderr)
                    repeated = self._activation_run(root, "recover")
                    self.assertEqual(repeated.returncode, 0, repeated.stderr)
                    self.assertIn("activation_recovered=no", repeated.stdout)
                    self.assertEqual(
                        (root / "srv/trading-bot-witness/current").resolve(),
                        paths["old_release"].resolve(),
                    )
                    self.assertFalse(paths["release"].exists())
                    self.assertFalse(paths["venv"].exists())
                    self.assertFalse(paths["activation"].exists())

    def test_activation_rollback_journal_survives_until_explicit_service_completion(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-activation-services-") as directory:
            root = Path(directory)
            release_id = "service-completion"
            self._prepare_activation_host(root, release_id)
            self._begin_and_stage_activation(root, release_id)
            killed = self._activation_run(
                root,
                "publish",
                release_id=release_id,
                kill_after="new_active_switched",
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL)

            recovered = self._activation_run(
                root,
                "recover",
                complete_rollback=False,
            )
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertIn("rolled-back-pending-service-completion", recovered.stdout)
            journal_path = root / "var/lib/trading-bot-witness/activation-state/active.json"
            journal_before = journal_path.read_bytes()
            journal = json.loads(journal_before)
            self.assertEqual(
                journal["phase"], "rolled_back_pending_service_completion"
            )
            self.assertEqual(set(journal["unit_states"]), set(ACTIVATION_MANAGED_UNITS))

            mismatched = self._activation_run(
                root,
                "recover",
                complete_rollback=False,
                host_toolchain_sha256="e" * 64,
            )
            self.assertNotEqual(mismatched.returncode, 0)
            self.assertIn("journal binding", mismatched.stderr)
            self.assertEqual(journal_path.read_bytes(), journal_before)

            repeated = self._activation_run(
                root,
                "recover",
                complete_rollback=False,
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(journal_path.read_bytes(), journal_before)
            blocked = self._activation_run(
                root,
                "begin",
                release_id="must-not-start",
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("requires service completion", blocked.stderr)

            completed = self._activation_run(
                root,
                "complete-rollback",
                release_id=release_id,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(journal_path.exists())

    def test_activation_commit_marker_survives_sigkill_without_rolling_back(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-activation-commit-") as directory:
            root = Path(directory)
            release_id = "committed-release"
            paths = self._prepare_activation_host(root, release_id)
            self._begin_and_stage_activation(root, release_id)
            published = self._activation_run(root, "publish", release_id=release_id)
            self.assertEqual(published.returncode, 0, published.stderr)
            killed = self._activation_run(
                root,
                "commit",
                release_id=release_id,
                kill_after="commit_recorded",
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL)
            recovered = self._activation_run(root, "recover")
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertIn(
                "activation_recovered=committed-pending-service-completion",
                recovered.stdout,
            )
            journal_path = (
                root / "var/lib/trading-bot-witness/activation-state/active.json"
            )
            committed_journal = journal_path.read_bytes()
            blocked = self._activation_run(
                root,
                "begin",
                release_id="different-release",
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("requires service completion", blocked.stderr)
            self.assertEqual(journal_path.read_bytes(), committed_journal)
            completed = self._activation_run(root, "complete", release_id=release_id)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                (root / "opt/trading-bot-witness/active").resolve(),
                paths["activation"].resolve(),
            )
            for item in ACTIVATION_MANAGED_FILES:
                destination = root / item.destination.lstrip("/")
                self.assertEqual(
                    destination.read_text(encoding="utf-8"),
                    f"new:{release_id}:{item.candidate}\n",
                )
            begun = self._activation_run(
                root,
                "begin",
                release_id="different-release",
            )
            self.assertEqual(begun.returncode, 0, begun.stderr)
            self.assertEqual(
                self._activation_run(root, "recover").returncode,
                0,
            )

    def test_activation_boot_recovery_defers_only_while_live_provision_lock_is_held(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-activation-boot-") as directory:
            root = Path(directory)
            release_id = "boot-recovery"
            paths = self._prepare_activation_host(root, release_id)
            self._begin_and_stage_activation(root, release_id)
            killed = self._activation_run(
                root,
                "publish",
                release_id=release_id,
                kill_after="new_active_switched",
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL)
            provision_lock = (
                root / "var/lib/trading-bot-witness/activation-state/.provision.lock"
            )
            provision_lock.touch(mode=0o600, exist_ok=True)
            descriptor = os.open(provision_lock, os.O_RDWR)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                deferred = self._activation_run(root, "recover-boot")
                self.assertEqual(deferred.returncode, 0, deferred.stderr)
                self.assertIn("deferred-live-provision", deferred.stdout)
                self.assertEqual(
                    (root / "opt/trading-bot-witness/active").resolve(),
                    paths["activation"].resolve(),
                )
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            recovered = self._activation_run(root, "recover-boot")
            self.assertNotEqual(recovered.returncode, 0)
            self.assertIn("pending exact service restoration", recovered.stderr)
            completed = self._activation_run(root, "recover")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                (root / "srv/trading-bot-witness/current").resolve(),
                paths["old_release"].resolve(),
            )

    def test_activation_boot_recovery_defers_while_hmac_rotation_lock_is_held(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-activation-rotation-") as directory:
            root = Path(directory)
            release_id = "rotation-recovery"
            paths = self._prepare_activation_host(root, release_id)
            self._begin_and_stage_activation(root, release_id)
            killed = self._activation_run(
                root,
                "publish",
                release_id=release_id,
                kill_after="new_active_switched",
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL)
            rotation_lock = (
                root / "var/lib/trading-bot-witness/hmac-rotation/.runtime.lock"
            )
            descriptor = os.open(rotation_lock, os.O_RDWR)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                deferred = self._activation_run(root, "recover-boot")
                self.assertEqual(deferred.returncode, 0, deferred.stderr)
                self.assertIn("deferred-live-rotation", deferred.stdout)
                self.assertEqual(
                    (root / "opt/trading-bot-witness/active").resolve(),
                    paths["activation"].resolve(),
                )
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            recovered = self._activation_run(root, "recover-boot")
            self.assertNotEqual(recovered.returncode, 0)
            self.assertIn("pending exact service restoration", recovered.stderr)
            completed = self._activation_run(root, "recover")
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_activation_never_claims_or_cleans_a_preexisting_release_path(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-activation-owned-") as directory:
            root = Path(directory)
            release_id = "already-present"
            paths = self._prepare_activation_host(root, release_id)
            paths["release"].mkdir(mode=0o755)
            sentinel = paths["release"] / "must-remain"
            sentinel.write_text("unowned\n", encoding="utf-8")
            begun = self._activation_run(root, "begin", release_id=release_id)
            self.assertNotEqual(begun.returncode, 0)
            self.assertIn("predates activation intent", begun.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unowned\n")
            recovered = self._activation_run(root, "recover")
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unowned\n")

    def test_activation_never_claims_a_preexisting_legacy_migration_path(self):
        for relative in (
            "opt/trading-bot-witness/activations/legacy-before-already-present",
            "opt/trading-bot-witness/venvs/legacy-before-already-present",
        ):
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory(
                    prefix="writer-witness-activation-legacy-owned-"
                ) as directory:
                    root = Path(directory)
                    release_id = "already-present"
                    self._prepare_activation_host(root, release_id)
                    unowned = root / relative
                    unowned.mkdir(mode=0o755)
                    sentinel = unowned / "must-remain"
                    sentinel.write_text("unowned legacy\n", encoding="utf-8")

                    begun = self._activation_run(root, "begin", release_id=release_id)

                    self.assertNotEqual(begun.returncode, 0)
                    self.assertIn("legacy migration path predates", begun.stderr)
                    self.assertEqual(
                        sentinel.read_text(encoding="utf-8"), "unowned legacy\n"
                    )
                    recovered = self._activation_run(root, "recover")
                    self.assertEqual(recovered.returncode, 0, recovered.stderr)
                    self.assertEqual(
                        sentinel.read_text(encoding="utf-8"), "unowned legacy\n"
                    )

    def test_activation_recovery_removes_only_journal_owned_temporaries(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-activation-temp-") as directory:
            root = Path(directory)
            release_id = "temporary-cleanup"
            self._prepare_activation_host(root, release_id)
            self._begin_and_stage_activation(root, release_id)
            killed = self._activation_run(
                root,
                "publish",
                release_id=release_id,
                kill_after="new_active_switched",
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL)
            journal_path = (
                root / "var/lib/trading-bot-witness/activation-state/active.json"
            )
            operation_id = json.loads(journal_path.read_text())["operation_id"]
            runtime_parent = root / "etc/trading-bot-witness"
            owned = runtime_parent / (
                f".runtime.env.activation-{operation_id}-{'a' * 32}"
            )
            owned.write_text("owned interrupted bytes\n", encoding="utf-8")
            owned.chmod(0o600)
            recovered = self._activation_run(root, "recover")
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertFalse(owned.exists())

            state = root / "var/lib/trading-bot-witness/activation-state"
            orphan = state / "operations" / ("b" * 32)
            orphan.mkdir(mode=0o700)
            (orphan / "secret").write_text("orphan\n", encoding="utf-8")
            cleaned = self._activation_run(root, "recover")
            self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
            self.assertFalse(orphan.exists())

    def test_clock_jump_fault_is_disposable_socket_only_and_never_changes_host_time(self):
        helper = (
            ROOT / "deploy/writer-witness/writer-witness-matrix-host-faults.sh"
        ).read_text(encoding="utf-8")
        runner = (
            ROOT / "scripts/run_writer_witness_real_host_matrix.py"
        ).read_text(encoding="utf-8")
        self.assertIn("FAKETIME_TIMESTAMP_FILE", helper)
        self.assertIn("FAKETIME_DONT_FAKE_MONOTONIC=1", helper)
        self.assertIn("--auth-local=peer --auth-host=reject", helper)
        self.assertIn("listen_addresses=''", helper)
        self.assertIn("production-before.json", helper)
        self.assertIn("production_state_unchanged", helper)
        self.assertIn('"synthetic_time_argument_used": False', runner)
        self.assertNotIn("--database-url", helper)
        for forbidden in (
            "date -s",
            "timedatectl set-",
            "hwclock",
            "chronyc makestep",
            "CAP_SYS_TIME",
        ):
            self.assertNotIn(forbidden, helper)

    def test_hmac_rotation_is_overlap_first_fail_closed_and_reversible(self):
        rotation = (
            ROOT / "deploy/writer-witness/writer-witness-rotate-hmac.py"
        ).read_text()
        self.assertIn('metadata["phase"] = "preparing"', rotation)
        self.assertIn('metadata["phase"] = "prepared"', rotation)
        self.assertIn('metadata["phase"] = "revoked"', rotation)
        self.assertIn("runtime-site.env.before", rotation)
        self.assertIn("runtime-site.env.overlap", rotation)
        self.assertIn("_snapshot_runtime_scope", rotation)
        self.assertIn("_restore_runtime_scope", rotation)
        self.assertIn('STATE_ROOT / ".runtime.lock"', rotation)
        self.assertIn("RENAME_NOREPLACE", rotation)
        self.assertIn("_delete_owned_state_directory", rotation)
        self.assertIn("_cleanup_and_attest_operation_temps", rotation)
        self.assertIn('"--leave-service-stopped"', rotation)
        self.assertIn("client.env.before", rotation)
        self.assertIn("_require_dark_state(expected_epoch)", rotation)
        self.assertIn("_restart_and_verify()", rotation)
        self.assertIn('/var/lib/trading-bot-witness/hmac-rotation', rotation)
        self.assertIn("os.replace(temporary, destination)", rotation)
        self.assertNotIn('/run/writer-witness-hmac-rotation', rotation)
        self.assertNotIn("print(new_secret", rotation)

    def test_smoke_client_can_prove_revoked_credentials_return_401(self):
        smoke = (ROOT / "scripts/smoke_writer_witness_client.py").read_text()
        self.assertIn('choices=(200, 401)', smoke)
        self.assertIn('if args.expect_http_status == 401:', smoke)

    def test_backup_and_restore_drill_do_not_require_runtime_credentials(self):
        backup = (ROOT / "deploy/writer-witness/writer-witness-backup.sh").read_text()
        restore = (ROOT / "deploy/writer-witness/writer-witness-restore-drill.sh").read_text()
        self.assertIn("runuser -u postgres -- pg_dump", backup)
        self.assertIn("sha256sum", backup)
        self.assertIn("runuser -u postgres -- pg_restore", restore)
        self.assertIn('<"$backup_path"', restore)
        self.assertIn("writer_witness_restore_drill_", restore)
        self.assertIn("trap cleanup EXIT", restore)
        self.assertNotIn("WITNESS_DB_RUNTIME_PASSWORD", backup + restore)
        self.assertIn('backup_path" == "-"', restore)

    def test_live_restore_is_explicit_guarded_and_keeps_rollback_database(self):
        restore = (
            ROOT / "deploy/writer-witness/writer-witness-live-restore.sh"
        ).read_text()
        runner = (
            ROOT / "scripts/run_writer_witness_offsite_live_restore.sh"
        ).read_text()
        self.assertIn('"--apply-from-stdin"', restore)
        self.assertIn("REQUIRED_CURRENT_STATE", restore)
        self.assertIn("EXPECTED_STATE", restore)
        self.assertIn("pg_restore --list", restore)
        self.assertIn("dd iflag=fullblock bs=1048576 count=64", restore)
        self.assertIn("dd bs=1 count=1", restore)
        self.assertNotIn('cat >&"$input_fd"', restore)
        self.assertIn("candidate_database", restore)
        self.assertIn("rollback_database", restore)
        self.assertIn("recover_from_journal", restore)
        self.assertIn("database_oid", restore)
        self.assertIn("current_oid", restore)
        self.assertIn("candidate_oid", restore)
        self.assertIn("WRITER_WITNESS_RESTORE_EXPECTED_MANIFEST_SHA256", restore)
        self.assertIn("WRITER_WITNESS_RESTORE_EXPECTED_BACKUP_SHA256", restore)
        self.assertIn("WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_MANIFEST_SHA256", restore)
        self.assertIn("WRITER_WITNESS_RESTORE_TEST_FAIL_AFTER", restore)
        for failpoint in (
            "input_validated", "candidate_created", "candidate_restored",
            "candidate_validated", "grants_applied", "prepared",
            "service_stopped", "current_disabled", "current_renamed",
            "candidate_promoted", "candidate_enabled", "service_started",
        ):
            self.assertIn(f"maybe_fail {failpoint}", restore)
        self.assertIn("writer-witness-state-manifest", restore)
        self.assertIn("database_exists", restore)
        self.assertIn("writer_witness_(candidate|rollback|failed)", restore)
        self.assertIn("WRITER_WITNESS_RESTORE_OPERATION_TAG", restore)
        self.assertIn('"no-recovery-required"', restore)
        self.assertIn('systemctl is-active --quiet "$SERVICE"', restore)
        self.assertIn('"$(manifest_hash writer_witness)" == "$guard_manifest"', restore)
        self.assertIn('"$orphan_count" == 0 && "$enabled_aux" == 0', restore)
        self.assertIn('systemctl stop "$SERVICE" || true', restore)
        self.assertIn("candidate_oid_from_operation", restore)
        self.assertIn("TEMPLATE template0 OID $candidate_oid", restore)
        self.assertIn('"$expected_oid" == 0 || "$observed_oid" != "$expected_oid"', restore)
        self.assertIn('DROP DATABASE $database_name', restore)
        self.assertNotIn("dropdb --if-exists writer_witness", restore)
        self.assertIn('"--apply"', runner)
        self.assertIn("download_writer_witness_s3_backup.py", runner)
        self.assertIn("age --decrypt", runner)
        self.assertIn("EXPECTED_MANIFEST_SHA256", runner)
        self.assertIn("EXPECTED_BACKUP_SHA256", runner)

    def test_live_restore_input_intent_survives_real_kill_points_and_recovers(self):
        payload = b"owned restore input\n" * 64
        for kill_point, input_state in (
            ("intent_recorded", "absent"),
            ("input_opened", "empty"),
            ("input_fsynced", "complete"),
            ("input_deleted", "absent"),
            ("journal_moved", "absent"),
        ):
            with self.subTest(kill_point=kill_point):
                with tempfile.TemporaryDirectory(prefix="restore-input-") as directory:
                    root = Path(directory)
                    state_root = root / "state"
                    backup_root = root / "backups"
                    killed = self._run_restore_input_primitive(
                        state_root=state_root,
                        backup_root=backup_root,
                        action="apply",
                        payload=payload,
                        kill_after=kill_point,
                    )
                    self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)
                    journal = state_root / "active.env"
                    if kill_point == "journal_moved":
                        self.assertFalse(journal.exists())
                    else:
                        self.assertTrue(journal.is_file())
                        journal_text = journal.read_text(encoding="utf-8")
                        self.assertIn("journal_version=3", journal_text)
                        self.assertRegex(
                            journal_text,
                            r"(?m)^operation_id=[0-9a-f]{32}$",
                        )
                    inputs = list(backup_root.glob(".replacement-restore.*.dump"))
                    if input_state == "absent":
                        self.assertEqual(inputs, [])
                    elif input_state == "empty":
                        self.assertEqual(len(inputs), 1)
                        self.assertEqual(inputs[0].stat().st_size, 0)
                        self.assertLessEqual(journal.stat().st_mtime_ns, inputs[0].stat().st_mtime_ns)
                    else:
                        self.assertEqual(len(inputs), 1)
                        self.assertEqual(inputs[0].read_bytes(), payload)
                        self.assertLessEqual(journal.stat().st_mtime_ns, inputs[0].stat().st_mtime_ns)

                    recovered = self._run_restore_input_primitive(
                        state_root=state_root,
                        backup_root=backup_root,
                        action="recover",
                    )
                    self.assertEqual(recovered.returncode, 0, recovered.stderr)
                    self.assertFalse(journal.exists())
                    self.assertEqual(
                        list(backup_root.glob(".replacement-restore.*.dump")), []
                    )
                    self.assertEqual(len(list((state_root / "history").glob("*.env"))), 1)
                    repeated = self._run_restore_input_primitive(
                        state_root=state_root,
                        backup_root=backup_root,
                        action="recover",
                    )
                    self.assertEqual(repeated.returncode, 0, repeated.stderr)

    def test_live_restore_serializes_every_operation_with_a_nonblocking_directory_lock(self):
        with tempfile.TemporaryDirectory(prefix="restore-lock-") as directory:
            root = Path(directory)
            state_root = root / "state"
            backup_root = root / "backups"
            state_root.mkdir(mode=0o700)
            backup_root.mkdir(mode=0o700)
            descriptor = os.open(state_root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                blocked = self._run_restore_input_primitive(
                    state_root=state_root,
                    backup_root=backup_root,
                    action="recover",
                )
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

            self.assertEqual(blocked.returncode, 75, blocked.stderr)
            self.assertIn(b"already active", blocked.stderr)
            self.assertFalse((state_root / "active.env").exists())

    def test_live_restore_initial_journal_publication_never_replaces_an_active_intent(self):
        with tempfile.TemporaryDirectory(prefix="restore-no-replace-") as directory:
            root = Path(directory)
            state_root = root / "state"
            backup_root = root / "backups"
            first = self._run_restore_input_primitive(
                state_root=state_root,
                backup_root=backup_root,
                action="apply",
                payload=b"first operation",
                kill_after="intent_recorded",
            )
            self.assertEqual(first.returncode, -signal.SIGKILL, first.stderr)
            journal = state_root / "active.env"
            original_journal = journal.read_bytes()

            competing = self._run_restore_input_primitive(
                state_root=state_root,
                backup_root=backup_root,
                action="apply",
                payload=b"competing operation",
            )

            self.assertNotEqual(competing.returncode, 0)
            self.assertEqual(journal.read_bytes(), original_journal)
            self.assertEqual(
                list(backup_root.glob(".replacement-restore.*.dump")), []
            )
            recovered = self._run_restore_input_primitive(
                state_root=state_root,
                backup_root=backup_root,
                action="recover",
            )
            self.assertEqual(recovered.returncode, 0, recovered.stderr)

    def test_live_restore_reconciles_only_owned_journal_temps_after_real_sigkill(self):
        payload = b"journal temp crash recovery\n" * 32
        for kill_point, expected_active, expected_temp_links in (
            ("intent_recorded_journal_temp_fsynced", False, 1),
            ("intent_recorded_journal_linked", True, 2),
            ("input_validated_journal_temp_fsynced", True, 1),
            ("input_validated_journal_replaced", True, 0),
        ):
            with self.subTest(kill_point=kill_point):
                with tempfile.TemporaryDirectory(prefix="restore-journal-temp-") as directory:
                    root = Path(directory)
                    state_root = root / "state"
                    backup_root = root / "backups"
                    killed = self._run_restore_input_primitive(
                        state_root=state_root,
                        backup_root=backup_root,
                        action="apply",
                        payload=payload,
                        kill_after=kill_point,
                    )
                    self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)
                    journal = state_root / "active.env"
                    self.assertEqual(journal.exists(), expected_active)
                    temps = list(state_root.glob(".active.*.env"))
                    if expected_temp_links:
                        self.assertEqual(len(temps), 1)
                        self.assertEqual(temps[0].stat().st_nlink, expected_temp_links)
                        if expected_active and expected_temp_links == 2:
                            self.assertTrue(os.path.samefile(temps[0], journal))
                    else:
                        self.assertEqual(temps, [])

                    recovered = self._run_restore_input_primitive(
                        state_root=state_root,
                        backup_root=backup_root,
                        action="recover",
                    )
                    self.assertEqual(recovered.returncode, 0, recovered.stderr)
                    self.assertFalse(journal.exists())
                    self.assertEqual(list(state_root.glob(".active.*.env")), [])
                    self.assertEqual(
                        list(backup_root.glob(".replacement-restore.*.dump")), []
                    )
                    repeated = self._run_restore_input_primitive(
                        state_root=state_root,
                        backup_root=backup_root,
                        action="recover",
                    )
                    self.assertEqual(repeated.returncode, 0, repeated.stderr)

    def test_live_restore_preserves_an_unowned_or_malformed_journal_temp(self):
        with tempfile.TemporaryDirectory(prefix="restore-foreign-temp-") as directory:
            root = Path(directory)
            state_root = root / "state"
            backup_root = root / "backups"
            state_root.mkdir(mode=0o700)
            backup_root.mkdir(mode=0o700)
            foreign = state_root / f".active.{'a' * 32}.ABCDEFGH.env"
            foreign.write_text("not an owned restore journal\n", encoding="utf-8")
            foreign.chmod(0o600)

            recovered = self._run_restore_input_primitive(
                state_root=state_root,
                backup_root=backup_root,
                action="recover",
            )

            self.assertNotEqual(recovered.returncode, 0)
            self.assertIn(b"not safely owned", recovered.stderr)
            self.assertEqual(foreign.read_text(), "not an owned restore journal\n")

    def test_all_live_restore_database_failpoints_are_true_sigkill_and_recover_repeats(self):
        failpoints = (
            "input_validated", "candidate_created", "candidate_restored",
            "candidate_validated", "grants_applied", "prepared",
            "service_stopped", "current_disabled", "current_renamed",
            "candidate_promoted", "candidate_enabled", "service_started",
        )
        for failpoint in failpoints:
            with self.subTest(failpoint=failpoint):
                with tempfile.TemporaryDirectory(prefix="restore-failpoint-") as directory:
                    root = Path(directory)
                    state_root = root / "state"
                    backup_root = root / "backups"
                    killed = self._run_restore_input_primitive(
                        state_root=state_root,
                        backup_root=backup_root,
                        action="failpoint",
                        fail_after=failpoint,
                    )
                    self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)
                    recovered = self._run_restore_input_primitive(
                        state_root=state_root,
                        backup_root=backup_root,
                        action="recover",
                    )
                    self.assertEqual(recovered.returncode, 0, recovered.stderr)
                    repeated = self._run_restore_input_primitive(
                        state_root=state_root,
                        backup_root=backup_root,
                        action="recover",
                    )
                    self.assertEqual(repeated.returncode, 0, repeated.stderr)

    def test_live_restore_refuses_unjournaled_orphan_input_without_deleting_it(self):
        with tempfile.TemporaryDirectory(prefix="restore-input-") as directory:
            root = Path(directory)
            state_root = root / "state"
            backup_root = root / "backups"
            backup_root.mkdir(mode=0o700)
            orphan = backup_root / ".replacement-restore.20990101000000_999.dump"
            orphan.write_bytes(b"ambiguous orphan")
            orphan.chmod(0o600)

            recovered = self._run_restore_input_primitive(
                state_root=state_root,
                backup_root=backup_root,
                action="recover",
            )

            self.assertNotEqual(recovered.returncode, 0)
            self.assertIn(b"unowned restore input", recovered.stderr)
            self.assertEqual(orphan.read_bytes(), b"ambiguous orphan")

    def test_live_restore_refuses_ambiguous_owned_input_without_deleting_it(self):
        payload = b"restore input that must remain untouched"
        with tempfile.TemporaryDirectory(prefix="restore-input-") as directory:
            root = Path(directory)
            state_root = root / "state"
            backup_root = root / "backups"
            killed = self._run_restore_input_primitive(
                state_root=state_root,
                backup_root=backup_root,
                action="apply",
                payload=payload,
                kill_after="input_fsynced",
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)
            [owned_input] = list(backup_root.glob(".replacement-restore.*.dump"))
            sentinel = root / "sentinel.dump"
            sentinel.write_bytes(b"do not delete")
            owned_input.unlink()
            owned_input.symlink_to(sentinel)

            recovered = self._run_restore_input_primitive(
                state_root=state_root,
                backup_root=backup_root,
                action="recover",
            )

            self.assertNotEqual(recovered.returncode, 0)
            self.assertIn(b"ownership is ambiguous", recovered.stderr)
            self.assertTrue(owned_input.is_symlink())
            self.assertEqual(sentinel.read_bytes(), b"do not delete")
            self.assertTrue((state_root / "active.env").is_file())

    def test_live_restore_refuses_input_outside_exact_active_journal_ownership(self):
        payload = b"exact journal-owned restore input"
        with tempfile.TemporaryDirectory(prefix="restore-input-") as directory:
            root = Path(directory)
            state_root = root / "state"
            backup_root = root / "backups"
            killed = self._run_restore_input_primitive(
                state_root=state_root,
                backup_root=backup_root,
                action="apply",
                payload=payload,
                kill_after="input_fsynced",
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)
            owned_inputs = list(backup_root.glob(".replacement-restore.*.dump"))
            self.assertEqual(len(owned_inputs), 1)
            foreign_input = backup_root / ".replacement-restore.20990101000000_999.dump"
            foreign_input.write_bytes(b"not journal owned")
            foreign_input.chmod(0o600)

            recovered = self._run_restore_input_primitive(
                state_root=state_root,
                backup_root=backup_root,
                action="recover",
            )

            self.assertNotEqual(recovered.returncode, 0)
            self.assertIn(b"outside the exact active journal", recovered.stderr)
            self.assertEqual(owned_inputs[0].read_bytes(), payload)
            self.assertEqual(foreign_input.read_bytes(), b"not journal owned")
            self.assertTrue((state_root / "active.env").is_file())

    def test_live_restore_refuses_a_dangling_journal_symlink(self):
        with tempfile.TemporaryDirectory(prefix="restore-input-") as directory:
            root = Path(directory)
            state_root = root / "state"
            backup_root = root / "backups"
            state_root.mkdir(mode=0o700)
            (state_root / "active.env").symlink_to(root / "missing-journal")

            recovered = self._run_restore_input_primitive(
                state_root=state_root,
                backup_root=backup_root,
                action="recover",
            )

            self.assertNotEqual(recovered.returncode, 0)
            self.assertIn(b"journal is missing or unsafe", recovered.stderr)
            self.assertTrue((state_root / "active.env").is_symlink())

    def test_offsite_s3_backup_keeps_decryption_identity_off_witness(self):
        sender = (ROOT / "deploy/writer-witness/writer-witness-offsite-backup.sh").read_text()
        uploader = (
            ROOT / "deploy/writer-witness/writer-witness-s3-put.py"
        ).read_text()
        configure = (
            ROOT / "scripts/configure_writer_witness_s3_backup.sh"
        ).read_text()
        service = (
            ROOT / "deploy/writer-witness/writer-witness-offsite-backup.service"
        ).read_text()
        timer = (
            ROOT / "deploy/writer-witness/writer-witness-offsite-backup.timer"
        ).read_text()
        self.assertIn("--recipients-file", sender)
        self.assertNotIn("--identity", sender)
        self.assertIn("writer-witness-s3-put", sender)
        self.assertIn("MAX_BACKUP_AGE_SECONDS", sender)
        self.assertIn('"PUT"', uploader)
        self.assertNotIn('connection.request("GET"', uploader)
        self.assertNotIn('connection.request("DELETE"', uploader)
        self.assertIn("private_decryption_key_present\":false", configure)
        self.assertNotIn("object-storage-admin.env", configure)
        self.assertIn("isolated_system_python -", configure)
        self.assertIn("/usr/bin/python3.12", configure)
        self.assertNotIn("\npython3 -", configure)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("OnUnitInactiveSec=1h", timer)

    def test_object_storage_provisioning_uses_enforced_explicit_denies(self):
        provision = (
            ROOT / "scripts/provision_writer_witness_object_storage.py"
        ).read_text()
        self.assertIn('"Mode") != "COMPLIANCE"', provision)
        self.assertIn('xml_text(root, "Days") != "90"', provision)
        self.assertIn('"s3:ListBucket"', provision)
        self.assertIn('"s3:GetObject"', provision)
        self.assertIn('"s3:DeleteObject"', provision)
        self.assertNotIn('"NotAction"', provision)
        self.assertIn("verify_uploader_boundary", provision)
        self.assertIn("uploader unexpectedly listed", provision)
        self.assertIn("uploader unexpectedly wrote outside witness prefix", provision)
        self.assertIn("uploader unexpectedly read", provision)
        self.assertIn("uploader unexpectedly deleted", provision)

    def test_arvan_recovery_vps_provisioning_is_dry_run_first_and_key_only(self):
        provision = (
            ROOT / "scripts/provision_arvan_witness_recovery_vps.py"
        ).read_text()
        self.assertIn('parser.add_argument("--apply", action="store_true")', provision)
        self.assertIn('parser.add_argument("--token-file"', provision)
        self.assertIn('STATE_FILE = Path("/root/secure-envs/arvan/', provision)
        self.assertIn("os.O_EXCL, 0o600", provision)
        self.assertIn("PasswordAuthentication no", provision)
        self.assertIn("KbdInteractiveAuthentication no", provision)
        self.assertIn("PermitRootLogin prohibit-password", provision)
        self.assertIn("ufw default deny incoming", provision)
        self.assertIn("65.109.216.187/32", provision)
        self.assertIn("65.109.220.59/32", provision)
        self.assertIn("87.236.212.194/32", provision)
        self.assertIn("validate_init_script(public_key)", provision)
        self.assertIn('"port_to": "443"', provision)
        self.assertIn('"password_printed": False', provision)
        self.assertNotIn("ARVAN_API_KEY=", provision)

    def test_authenticated_smoke_is_read_only_and_redacts_client_secret(self):
        smoke = (ROOT / "scripts/smoke_writer_witness_client.py").read_text()
        self.assertIn('STATUS_PATH = "/v1/writer-witness/status"', smoke)
        self.assertIn('"GET"', smoke)
        self.assertNotIn('method="POST"', smoke)
        self.assertNotIn('"secret": secret', smoke)
        self.assertIn('Path(sys.executable)', smoke)
        self.assertNotIn('Path(sys.prefix)', smoke)


if __name__ == "__main__":
    unittest.main()
